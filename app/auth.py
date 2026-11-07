"""Supported ChatGPT plan OAuth. Credentials never reach app JavaScript."""
import asyncio
import base64
import hashlib
import json
import os
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

AUTHORITY = "https://auth.openai.com"
AUTHORIZE = AUTHORITY + "/api/accounts/authorize"
TOKEN = AUTHORITY + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke " + PLAN_SCOPE


class AuthError(Exception):
    pass


def config_directory():
    if os.environ.get("REEL_MAP_CONFIG_DIR"):
        return Path(os.environ["REEL_MAP_CONFIG_DIR"]).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "ReelMap"
    if __import__("sys").platform == "darwin":
        return Path.home() / "Library/Application Support/ReelMap"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "reel-map"


def protected_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".account-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class PlanAccount:
    def __init__(self, directory=None):
        self.directory = Path(directory or config_directory())
        self.path = self.directory / "account.json"
        self.lock = asyncio.Lock()
        self.pending = {}

    def load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def host_id(self):
        path = self.directory / "host.json"
        if path.exists():
            return json.loads(path.read_text())["id"]
        value = "urn:uuid:" + str(uuid.uuid4())
        protected_write(path, {"id": value})
        return value

    def status(self):
        data = self.load()
        return {"connected": bool(data.get("access_token")) and PLAN_SCOPE in data.get("scopes", []),
                "account": data.get("email") or ("ChatGPT account" if data.get("subject") else None)}

    def begin(self, redirect_uri):
        self.pending = {k: v for k, v in self.pending.items() if v["expires"] > time.time()}
        if len(self.pending) >= 5:
            raise AuthError("Too many sign-in attempts. Wait a few minutes and try again.")
        data = self.load()
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        attempt = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params = {"client_id": data.get("client_id") or "dynamic_agent_client", "ext_agent_host_id": self.host_id(),
                  "response_type": "code", "redirect_uri": redirect_uri, "scope": SCOPES, "resource": RESOURCE,
                  "state": state, "nonce": nonce, "code_challenge_method": "S256", "code_challenge": challenge}
        if not data.get("client_id"):
            params["agent_name_hint"] = "Reel Map"
        elif data.get("id_token"):
            params["id_token_hint"] = data["id_token"]
        self.pending[attempt] = {"state": state, "nonce": nonce, "verifier": verifier, "redirect_uri": redirect_uri,
                                 "client_id": data.get("client_id"), "subject": data.get("subject"), "expires": time.time() + 600}
        return attempt, AUTHORIZE + "?" + urlencode(params)

    async def discovery(self, client):
        r = await client.get(AUTHORITY + "/.well-known/openid-configuration")
        r.raise_for_status()
        data = r.json()
        if data.get("issuer") != AUTHORITY:
            raise AuthError("Unexpected OpenAI identity issuer.")
        for field in ("jwks_uri", "revocation_endpoint"):
            if data.get(field):
                u = urlsplit(data[field])
                if u.scheme != "https" or u.hostname != "auth.openai.com":
                    raise AuthError("Unexpected OpenAI identity endpoint.")
        return data

    async def finish(self, attempt, query):
        pending = self.pending.pop(attempt or "", None)
        if not pending or pending["expires"] < time.time() or not secrets.compare_digest(query.get("state", ""), pending["state"]):
            raise AuthError("Sign-in expired or its security check failed. Please start again.")
        if query.get("error"):
            raise AuthError("ChatGPT authorization was declined or could not be completed.")
        client_id = query.get("client_id") or pending["client_id"]
        if not client_id or client_id == "dynamic_agent_client" or (pending["client_id"] and client_id != pending["client_id"]):
            raise AuthError("ChatGPT did not return the expected client registration.")
        if not query.get("code"):
            raise AuthError("ChatGPT did not return an authorization code.")
        async with self.lock, httpx.AsyncClient(timeout=30) as client:
            r = await client.post(TOKEN, data={"grant_type": "authorization_code", "client_id": client_id,
                "code": query["code"], "code_verifier": pending["verifier"], "redirect_uri": pending["redirect_uri"], "resource": RESOURCE})
            if not r.is_success:
                raise AuthError("ChatGPT could not exchange this sign-in. Start a fresh sign-in attempt.")
            tokens = r.json()
            discovery = await self.discovery(client)
            keys_response = await client.get(discovery["jwks_uri"])
            keys_response.raise_for_status()
            header = jwt.get_unverified_header(tokens["id_token"])
            if header.get("alg") not in ("RS256", "ES256"):
                raise AuthError("Unsupported ID-token signature.")
            key = next((k for k in keys_response.json()["keys"] if k.get("kid") == header.get("kid")), None)
            if not key:
                raise AuthError("Could not verify the ChatGPT identity signature.")
            claims = jwt.decode(tokens["id_token"], jwt.PyJWK.from_dict(key).key, algorithms=[header["alg"]],
                                audience=client_id, issuer=AUTHORITY, options={"require": ["exp", "iat", "sub", "iss", "aud", "nonce"]})
            if not secrets.compare_digest(str(claims["nonce"]), pending["nonce"]):
                raise AuthError("The sign-in nonce did not match.")
            if pending["subject"] and claims["sub"] != pending["subject"]:
                raise AuthError("Sign in with the originally connected account, or disconnect it first.")
            scopes = tokens.get("scope", "").split()
            if PLAN_SCOPE not in scopes:
                raise AuthError("This authorization does not allow use of your ChatGPT plan. Enable plan access during sign-in.")
            data = {**tokens, "scopes": scopes, "client_id": client_id, "subject": claims["sub"],
                    "email": claims.get("email"), "expires_at": time.time() + int(tokens.get("expires_in", 3600))}
            protected_write(self.path, data)

    async def access_token(self):
        async with self.lock:
            data = self.load()
            if not data.get("access_token") or PLAN_SCOPE not in data.get("scopes", []):
                raise AuthError("Connect your ChatGPT plan first.")
            if data.get("expires_at", 0) > time.time() + 90:
                return data["access_token"]
            if not data.get("refresh_token"):
                raise AuthError("Your sign-in expired. Connect your ChatGPT account again.")
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(TOKEN, data={"grant_type": "refresh_token", "client_id": data["client_id"],
                    "refresh_token": data["refresh_token"], "resource": RESOURCE})
            if not r.is_success:
                raise AuthError("ChatGPT authorization could not be renewed. Sign in again; API-key billing will not be used.")
            fresh = r.json()
            scopes = fresh.get("scope", " ".join(data["scopes"])).split()
            if PLAN_SCOPE not in scopes:
                raise AuthError("Permission to use your ChatGPT plan is no longer granted.")
            data.update(fresh, scopes=scopes, expires_at=time.time() + int(fresh.get("expires_in", 3600)))
            protected_write(self.path, data)
            return data["access_token"]

    async def disconnect(self):
        async with self.lock:
            data = self.load()
            confirmed = not data.get("refresh_token")
            if data.get("refresh_token"):
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        info = await self.discovery(client)
                        r = await client.post(info["revocation_endpoint"], data={"token": data["refresh_token"],
                            "token_type_hint": "refresh_token", "client_id": data["client_id"]})
                        confirmed = r.status_code == 200
                except (httpx.HTTPError, AuthError, KeyError):
                    pass
            # Retain the registration identity for later reauthorization, but
            # remove every credential. It remains bound to the same account.
            if self.path.exists():
                protected_write(self.path, {k: data[k] for k in ("client_id", "subject", "email") if k in data})
            self.pending.clear()
            return confirmed
