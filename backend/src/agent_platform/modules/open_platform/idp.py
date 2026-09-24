"""Verified identities from OIDC and first-party enterprise authorization-code flows."""

import base64
import hashlib
import hmac
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

from agent_platform.platform.persistence.store import DomainError


def safe_url(value):
    p = urlsplit(value)
    if (
        not p.hostname
        or p.username
        or p.password
        or p.fragment
        or any(ord(c) < 33 for c in value)
        or (
            p.scheme != "https"
            and not (p.scheme == "http" and p.hostname in {"localhost", "127.0.0.1", "::1"})
        )
    ):
        raise ValueError("https_or_loopback_url_required")
    _ = p.port
    return value


def origin(value):
    safe_url(value)
    p = urlsplit(value)
    if p.path not in {"", "/"} or p.query:
        raise ValueError("exact_origin_required")
    return value.rstrip("/")


def challenge(verifier):
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


class IdentityProviders:
    async def fetch(self, method, url, **kwargs):
        safe_url(url)
        try:
            async with (
                httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client,
                client.stream(method, url, **kwargs) as response,
            ):
                response.raise_for_status()
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 1_000_000:
                        raise ValueError("response_too_large")
                import json

                result = json.loads(chunks)
            if (
                not isinstance(result, dict)
                or result.get("error")
                or result.get("errcode", 0)
                or result.get("code", 0)
            ):
                raise ValueError("provider_error")
            return result
        except (httpx.HTTPError, ValueError, TypeError):
            raise DomainError("identity_provider_request_failed", 502) from None

    async def metadata(self, config):
        m = await self.fetch(
            "GET", config["issuer"].rstrip("/") + "/.well-known/openid-configuration"
        )
        if m.get("issuer") != config["issuer"] or "S256" not in m.get(
            "code_challenge_methods_supported", []
        ):
            raise DomainError("oidc_issuer_or_pkce_unsupported", 422)
        for field in ["authorization_endpoint", "token_endpoint", "jwks_uri"]:
            safe_url(m[field])
        return m

    async def authorize(self, provider, state, nonce, verifier, callback):
        c, kind = provider["config"], provider["kind"]
        params = {"redirect_uri": callback, "state": state, "response_type": "code"}
        if kind == "oidc":
            m = await self.metadata(c)
            url = m["authorization_endpoint"]
            params.update(
                client_id=c["client_id"],
                scope="openid profile",
                nonce=nonce,
                code_challenge=challenge(verifier),
                code_challenge_method="S256",
            )
        elif kind == "wecom":
            url = "https://open.work.weixin.qq.com/wwopen/sso/qrConnect"
            params.update(appid=c["organization"], agentid=c["agent_id"])
        elif kind == "dingtalk":
            url = "https://login.dingtalk.com/oauth2/auth"
            params.update(client_id=c["client_id"], scope="openid", prompt="consent")
        else:
            url = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
            params.update(client_id=c["client_id"], scope="contact:user.base:readonly")
        return url + ("&" if "?" in url else "?") + urlencode(params)

    async def exchange(self, provider, secret, code, payload, callback):
        c, kind = provider["config"], provider["kind"]
        if kind == "oidc":
            m = await self.metadata(c)
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback,
                "client_id": c["client_id"],
                "code_verifier": payload["verifier"],
            }
            kwargs = {"data": data}
            if c.get("token_auth", "client_secret_basic") == "client_secret_post":
                data["client_secret"] = secret
            else:
                kwargs["auth"] = httpx.BasicAuth(c["client_id"], secret)
            token = await self.fetch("POST", m["token_endpoint"], **kwargs)
            keys = await self.fetch("GET", m["jwks_uri"])
            try:
                encoded = token["id_token"]
                header = jwt.get_unverified_header(encoded)
                if header.get("alg") not in {"RS256", "ES256"}:
                    raise ValueError("unsupported_algorithm")
                candidates = [
                    k
                    for k in keys["keys"]
                    if k.get("kid") == header.get("kid")
                    and k.get("use", "sig") == "sig"
                    and k.get("alg", header["alg"]) == header["alg"]
                ]
                if len(candidates) != 1:
                    raise ValueError("ambiguous_key")
                key = jwt.PyJWK.from_dict(candidates[0], algorithm=header["alg"])
                claims = jwt.decode(
                    encoded,
                    key.key,
                    algorithms=[header["alg"]],
                    audience=c["client_id"],
                    issuer=c["issuer"],
                    options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
                    leeway=30,
                )
                if (
                    not hmac.compare_digest(str(claims["nonce"]), payload["nonce"])
                    or ("azp" in claims and claims["azp"] != c["client_id"])
                    or (
                        isinstance(claims["aud"], list)
                        and len(claims["aud"]) > 1
                        and claims.get("azp") != c["client_id"]
                    )
                ):
                    raise ValueError("claim_mismatch")
                subject, name = claims["sub"], claims.get("name", claims["sub"])
            except (jwt.PyJWTError, ValueError, KeyError, TypeError):
                raise DomainError("invalid_oidc_identity", 401) from None
        elif kind == "wecom":
            token = await self.fetch(
                "GET",
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": c["organization"], "corpsecret": secret},
            )
            profile = await self.fetch(
                "GET",
                "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo",
                params={"access_token": token["access_token"], "code": code},
            )
            subject = profile.get("userid") or profile.get("UserId")
            name = subject
        elif kind == "dingtalk":
            token = await self.fetch(
                "POST",
                "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
                json={
                    "clientId": c["client_id"],
                    "clientSecret": secret,
                    "code": code,
                    "grantType": "authorization_code",
                },
            )
            profile = await self.fetch(
                "GET",
                "https://api.dingtalk.com/v1.0/contact/users/me",
                headers={"x-acs-dingtalk-access-token": token["accessToken"]},
            )
            app_token = await self.fetch(
                "POST",
                "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                json={"appKey": c["client_id"], "appSecret": secret},
            )
            member = await self.fetch(
                "POST",
                "https://oapi.dingtalk.com/topapi/user/getbyunionid",
                params={"access_token": app_token["accessToken"]},
                json={"unionid": profile["unionId"]},
            )
            subject = member.get("result", {}).get("userid")
            name = profile.get("nick", subject)
        else:
            token = await self.fetch(
                "POST",
                "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
                json={
                    "grant_type": "authorization_code",
                    "client_id": c["client_id"],
                    "client_secret": secret,
                    "code": code,
                    "redirect_uri": callback,
                },
            )
            profile = await self.fetch(
                "GET",
                "https://open.feishu.cn/open-apis/authen/v1/user_info",
                headers={"Authorization": "Bearer " + token["access_token"]},
            )
            data = profile.get("data", {})
            if data.get("tenant_key") != c["organization"]:
                raise DomainError("enterprise_membership_required", 403)
            subject, name = data.get("open_id"), data.get("name")
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise DomainError("enterprise_membership_required", 403)
        return subject, str(name or subject)[:100]
