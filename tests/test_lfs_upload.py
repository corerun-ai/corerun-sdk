"""A push stores weights through the SDK's own LFS client, with no git-lfs.

Against a small in-process LFS server that speaks the same batch, PUT and
verify exchange as the platform: what is under test is that weights become
pointers git-lfs would recognise, that only objects the server lacks are sent,
that each arrives whole and is verified, and that our credential goes to our
server and not to a presigned URL.
"""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from corerun import lfs
from corerun.pull import _stage, _track


@pytest.fixture
def server():
    stored: dict = {}
    seen: list = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _body(self):
            return self.rfile.read(int(self.headers.get("Content-Length", 0)))

        def do_POST(self):
            body = json.loads(self._body())
            seen.append(("POST", self.path, self.headers.get("Authorization")))
            if self.path.endswith("/objects/batch"):
                base = f"http://{self.headers['Host']}"
                out = []
                for o in body["objects"]:
                    if o["oid"] in stored:
                        out.append({"oid": o["oid"], "size": o["size"]})
                        continue
                    # One object "presigned", the rest through this server.
                    href = f"{base}/store/{o['oid']}" + ("?X-Amz-Signature=x" if len(out) == 0 else "")
                    out.append({"oid": o["oid"], "size": o["size"], "actions": {
                        "upload": {"href": href}, "verify": {"href": f"{base}/verify"}}})
                reply = json.dumps({"transfer": "basic", "objects": out}).encode()
            else:  # verify
                ok = stored.get(body["oid"]) == body["size"]
                self.send_response(200 if ok else 404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.git-lfs+json")
            self.end_headers()
            self.wfile.write(reply)

        def do_PUT(self):
            data = self._body()
            oid = self.path.split("/store/")[1].split("?")[0]
            seen.append(("PUT", self.path, self.headers.get("Authorization")))
            assert hashlib.sha256(data).hexdigest() == oid
            stored[oid] = len(data)
            self.send_response(200)
            self.end_headers()

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/repo.git", stored, seen
    httpd.shutdown()


def test_weights_become_pointers_and_are_uploaded_once(tmp_path, server):
    url, stored, seen = server
    src = tmp_path / "model"
    (src / "sub").mkdir(parents=True)
    (src / "a.safetensors").write_bytes(b"A" * 300_000)
    (src / "sub" / "b.bin").write_bytes(b"B" * 5000)
    (src / "copy.safetensors").write_bytes(b"A" * 300_000)  # same content as a
    (src / "config.json").write_text("{}")

    target = tmp_path / "work" / "model"
    objects = _stage(src, target)
    _track(tmp_path / "work" / ".gitattributes")

    # Weights are pointers, byte for byte what git-lfs writes; the rest is copied.
    pointer = (target / "a.safetensors").read_text()
    assert pointer == (
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{hashlib.sha256(b'A' * 300_000).hexdigest()}\nsize 300000\n"
    )
    assert lfs.parse_pointer(target / "sub" / "b.bin") is not None
    assert (target / "config.json").read_text() == "{}"
    assert "*.safetensors filter=lfs diff=lfs merge=lfs -text" in (tmp_path / "work" / ".gitattributes").read_text()

    # Two distinct objects are sent, each once, and verified.
    assert lfs.upload_all(url, "crn_test", objects) == 2
    assert len(stored) == 2
    puts = [s for s in seen if s[0] == "PUT"]
    assert len(puts) == 2
    # Our credential to our server, none to the presigned URL.
    presigned = [p for p in puts if "X-Amz-Signature" in p[1]]
    proxied = [p for p in puts if "X-Amz-Signature" not in p[1]]
    assert presigned and presigned[0][2] is None
    assert proxied and proxied[0][2].startswith("Basic ")

    # A second push of the same weights sends nothing.
    assert lfs.upload_all(url, "crn_test", objects) == 0


@pytest.fixture
def multipart_server():
    """An LFS server that lays large objects out as parts of PART bytes."""
    PART = 1000
    state = {"parts": {}, "stored": {}, "largest": 0, "aborted": 0, "fail_part": None, "chunked": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _body(self):
            if self.headers.get("Transfer-Encoding") == "chunked":
                state["chunked"] += 1
            return self.rfile.read(int(self.headers.get("Content-Length", 0)))

        def do_POST(self):
            body = json.loads(self._body() or b"{}")
            base = f"http://{self.headers['Host']}"
            if self.path.endswith("/objects/batch"):
                assert body["transfers"][0] == "multipart-basic"
                out = []
                for o in body["objects"]:
                    oid, size = o["oid"], o["size"]
                    parts = [{"href": f"{base}/part/{oid}/{i + 1}", "pos": pos, "size": min(PART, size - pos)}
                             for i, pos in enumerate(range(0, size, PART))]
                    out.append({"oid": oid, "size": size, "actions": {
                        "parts": parts,
                        "commit": {"href": f"{base}/commit/{oid}"},
                        "abort": {"href": f"{base}/abort/{oid}"},
                    }})
                reply = json.dumps({"transfer": "multipart-basic", "objects": out}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(reply)
                return
            if self.path.startswith("/commit/"):
                oid = self.path.split("/")[2]
                numbers = [p["part_number"] for p in body["parts"]]
                assert numbers == list(range(1, len(numbers) + 1))
                assert all(p["etag"] == f'"e{p["part_number"]}"' for p in body["parts"])
                state["stored"][oid] = b"".join(state["parts"][(oid, n)] for n in numbers)
                self.send_response(200)
                self.end_headers()
                return
            if self.path.startswith("/abort/"):
                state["aborted"] += 1
                self.send_response(204)
                self.end_headers()

        def do_PUT(self):
            _, _, oid, n = self.path.split("/")
            data = self._body()
            state["largest"] = max(state["largest"], len(data))
            if state["fail_part"] == int(n):
                self.send_response(400)
                self.end_headers()
                return
            state["parts"][(oid, int(n))] = data
            self.send_response(200)
            self.send_header("ETag", f'"e{n}"')
            self.end_headers()

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/repo.git", state, PART
    httpd.shutdown()


def test_a_large_object_goes_up_in_parts(tmp_path, multipart_server):
    url, state, part = multipart_server
    path = tmp_path / "model.safetensors"
    data = bytes(range(256)) * 17  # 4352 bytes: five parts
    path.write_bytes(data)
    obj = lfs.hash_file(path)

    assert lfs.upload_all(url, "crn_test", [obj]) == 1
    assert state["stored"][obj.oid] == data
    assert state["largest"] <= part
    # Each part declares its length: a chunked body is what some stores and
    # edges refuse for a PUT.
    assert state["chunked"] == 0
    assert state["aborted"] == 0


def test_a_failed_part_aborts_the_upload(tmp_path, multipart_server):
    url, state, _ = multipart_server
    state["fail_part"] = 3
    path = tmp_path / "model.safetensors"
    path.write_bytes(b"x" * 4000)

    with pytest.raises(lfs.TransferError):
        lfs.upload_all(url, "crn_test", [lfs.hash_file(path)])
    assert state["aborted"] == 1
    assert not state["stored"]
