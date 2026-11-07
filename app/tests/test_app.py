import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import PlanAccount, AuthError, PLAN_SCOPE, protected_write
from app.server import create_app
from app import inference

ORIGIN = "http://127.0.0.1:8765"
REEL = "https://www.instagram.com/reel/DbyySxJuluW/"


class FakeAccount:
    def status(self):
        return {"connected": True, "account": "Test account"}

    async def access_token(self):
        return "test-oauth-token-not-an-api-key"


class LocalAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_boundary_rejects_rebinding_and_cross_site_writes(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(FakeAccount())), base_url=ORIGIN) as client:
            self.assertEqual((await client.get("/api/status")).status_code, 200)
            self.assertEqual((await client.get("/api/status", headers={"Host": "attacker.example"})).status_code, 403)
            self.assertEqual((await client.post("/api/auth/start", headers={"Origin": "https://attacker.example"})).status_code, 403)

    async def test_stateless_processing_cleans_media_and_has_no_history_endpoint(self):
        seen = []

        async def fake_media(url, directory, audio):
            seen.append(Path(directory))
            (Path(directory) / "private-video.mp4").write_bytes(b"fixture")
            return {"caption": "London", "images": [{"kind": "video_frames"}], "notes": [], "transcript": ""}

        async def fake_extract(*args):
            return {"title": "London", "places": [{"name": "British Museum", "address": "London", "evidence": "Label at 2s"}], "notes": ""}

        app = create_app(FakeAccount())
        with patch("app.server.media_process", side_effect=fake_media), patch("app.inference.models", AsyncMock(return_value=[{"id": "test-model"}])), patch("app.inference.extract", side_effect=fake_extract):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                r = await client.post("/api/v1/map", headers={"Origin": ORIGIN}, json={"url": REEL, "model": "test-model"})
                self.assertEqual(r.status_code, 200)
                self.assertIn("event: result", r.text)
                self.assertIn("British+Museum", r.text)
                self.assertEqual((await client.get("/api/history")).status_code, 404)
        self.assertTrue(seen)
        self.assertTrue(all(not directory.exists() for directory in seen))
        self.assertFalse(app.state.busy)

    async def test_processing_failure_also_cleans_temp_files(self):
        seen = []

        async def broken(url, directory, audio):
            seen.append(Path(directory))
            (Path(directory) / "partial-video.mp4").write_bytes(b"fixture")
            raise ValueError("Instagram blocked the download.")

        app = create_app(FakeAccount())
        with patch("app.server.media_process", side_effect=broken), patch("app.inference.models", AsyncMock(return_value=[{"id": "test-model"}])):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                r = await client.post("/api/v1/map", headers={"Origin": ORIGIN}, json={"url": REEL, "model": "test-model"})
                self.assertIn("event: error", r.text)
                self.assertNotIn("event: result", r.text)
        self.assertTrue(all(not directory.exists() for directory in seen))
        self.assertFalse(app.state.busy)


class OAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_state_rejection_and_stable_host_id(self):
        with tempfile.TemporaryDirectory() as directory:
            account = PlanAccount(directory)
            self.assertEqual(account.host_id(), account.host_id())
            attempt, url = account.begin(ORIGIN + "/auth/callback")
            query = parse_qs(urlsplit(url).query)
            self.assertEqual(query["client_id"], ["dynamic_agent_client"])
            self.assertEqual(query["code_challenge_method"], ["S256"])
            with self.assertRaises(AuthError):
                await account.finish(attempt, {"state": "wrong", "code": "fake", "client_id": "issued-client"})
            self.assertFalse(account.status()["connected"])

    async def test_oauth_verifies_signature_nonce_and_plan_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            account = PlanAccount(directory)
            attempt, url = account.begin(ORIGIN + "/auth/callback")
            params = parse_qs(urlsplit(url).query)
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
            public.update(kid="fixture-key", alg="RS256", use="sig")
            token = jwt.encode({"iss": "https://auth.openai.com", "aud": "issued-client", "sub": "fixture-subject", "iat": int(time.time()),
                "exp": int(time.time()) + 300, "nonce": params["nonce"][0], "email": "fixture@example.test"}, key, algorithm="RS256", headers={"kid": "fixture-key"})

            def handler(request):
                if request.url.path.endswith("/oauth/token"):
                    self.assertIn(b"code_verifier=", request.content)
                    return httpx.Response(200, json={"access_token": "fixture-access", "refresh_token": "fixture-refresh", "id_token": token,
                        "scope": PLAN_SCOPE, "expires_in": 3600})
                if request.url.path.endswith("openid-configuration"):
                    return httpx.Response(200, json={"issuer": "https://auth.openai.com", "jwks_uri": "https://auth.openai.com/keys"})
                return httpx.Response(200, json={"keys": [public]})

            real_client = httpx.AsyncClient
            with patch("app.auth.httpx.AsyncClient", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
                await account.finish(attempt, {"state": params["state"][0], "code": "fixture-code", "client_id": "issued-client"})
            self.assertTrue(account.status()["connected"])
            self.assertEqual(await account.access_token(), "fixture-access")
            if __import__("os").name != "nt":
                self.assertEqual(account.path.stat().st_mode & 0o777, 0o600)


class InferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_result_is_accepted_before_completed_event(self):
        for completed in (False, True):
            payload = json.dumps({"title": "London", "places": [], "notes": "No place names"})
            events = [{"type": "response.output_text.delta", "delta": payload}]
            if completed:
                events.append({"type": "response.completed"})

            def handler(request):
                data = json.loads(request.content)
                self.assertFalse(data["store"])
                self.assertTrue(data["stream"])
                self.assertEqual(str(request.url), "https://api.openai.com/v1/responses")
                self.assertEqual(request.headers["authorization"], "Bearer test-oauth-token-not-an-api-key")
                return httpx.Response(200, content="\n\n".join("data: " + json.dumps(e) for e in events) + "\n\n")

            real_client = httpx.AsyncClient
            with patch("app.inference.httpx.AsyncClient", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
                call = inference.extract(FakeAccount(), "test-model", {"caption": "", "images": [], "notes": []}, REEL)
                if completed:
                    result = await call
                    self.assertEqual(result["places"], [])
                else:
                    with self.assertRaises(inference.InferenceError):
                        await call


if __name__ == "__main__":
    unittest.main()
