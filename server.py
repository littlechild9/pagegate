"""
PageGate — 一个简单的 Python 服务器，用于发布、管理和分享 AI 生成的 HTML 页面。
"""

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import bcrypt
import httpx
import yaml
from urllib.parse import quote, urlencode

logger = logging.getLogger("pagegate")
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, TimestampSigner

# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
PAGES_DIR = BASE_DIR / "pages"
TEMPLATES_DIR = BASE_DIR / "templates"
SUPER_ADMIN_EMAIL = "__super_admin__"

DATA_DIR.mkdir(exist_ok=True)
PAGES_DIR.mkdir(exist_ok=True)

# 加载配置
with open(BASE_DIR / "config.yaml", "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

SUPER_ADMIN_TOKEN = CONFIG.get("admin_token", "")
SERVER_CONFIG = CONFIG.get("server", {})
BASE_URL = SERVER_CONFIG.get("base_url", "http://localhost:8888")
SESSION_SECRET = SERVER_CONFIG.get("session_secret", secrets.token_hex(32))
BRANDING_CONFIG = CONFIG.get("branding", {})
DEFAULT_PRODUCT_NAME = "PageGate"
DEFAULT_INSTANCE_NAME = "Xuan & Friends' PageGate"
PRODUCT_NAME = (BRANDING_CONFIG.get("product_name") or DEFAULT_PRODUCT_NAME).strip()
INSTANCE_NAME = (BRANDING_CONFIG.get("instance_name") or DEFAULT_INSTANCE_NAME).strip()

# 注册模式: "open" 允许任何人注册, "closed" 禁止注册
REGISTRATION_CONFIG = CONFIG.get("registration", {})
REGISTRATION_MODE = REGISTRATION_CONFIG.get("mode", "open")

# OpenClaw 配置
OPENCLAW_CONFIG = CONFIG.get("openclaw", {})
OPENCLAW_WEBHOOK_URL = OPENCLAW_CONFIG.get("webhook_url", "")
OPENCLAW_WEBHOOK_TOKEN = OPENCLAW_CONFIG.get("webhook_token", "")

signer = TimestampSigner(SESSION_SECRET)

app = FastAPI(title=PRODUCT_NAME, docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
EVENT_SUBSCRIBERS = {}
EVENT_HISTORY = []
EVENT_HISTORY_LIMIT = 200
APPROVAL_SUBSCRIBERS = {}
SSE_RETRY_MS = max(1000, int(SERVER_CONFIG.get("sse_retry_ms", 5000)))
SSE_HEARTBEAT_INTERVAL_SEC = max(10, int(SERVER_CONFIG.get("sse_heartbeat_interval_sec", 20)))
SSE_EVENT_QUEUE_SIZE = max(8, int(SERVER_CONFIG.get("sse_event_queue_size", 64)))
SSE_APPROVAL_QUEUE_SIZE = max(2, int(SERVER_CONFIG.get("sse_approval_queue_size", 8)))

def get_user_agent(request: Request) -> str:
    return (request.headers.get("user-agent") or "").lower()


def is_mobile_user_agent(user_agent: str) -> bool:
    mobile_markers = ["iphone", "ipad", "android", "mobile", "ios"]
    return any(marker in user_agent for marker in mobile_markers)


def is_dingtalk_user_agent(user_agent: str) -> bool:
    return "dingtalk" in user_agent or "aliapp(dingtalk" in user_agent


def build_dingtalk_mobile_launch_url(auth_url: str) -> str:
    return f"dingtalk://dingtalkclient/page/link?url={quote(auth_url, safe='')}"


def _format_sse_event(*, event: Optional[str] = None, data=None, event_id: str = "", retry_ms: Optional[int] = None, comment: str = "") -> str:
    lines = []
    if retry_ms is not None:
        lines.append(f"retry: {retry_ms}")
    if comment:
        lines.append(f": {comment}")
    if event_id:
        lines.append(f"id: {event_id}")
    if event:
        lines.append(f"event: {event}")
    if data is not None:
        payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        payload_lines = payload.splitlines() or [""]
        lines.extend(f"data: {line}" for line in payload_lines)
    return "\n".join(lines) + "\n\n"


def _event_belongs_to_subscriber(event: dict, subscriber: dict) -> bool:
    if subscriber.get("is_super"):
        return True
    slug = event.get("page", {}).get("slug", "")
    return slug in subscriber.get("slugs", frozenset())


def template_context(**kwargs) -> dict:
    context = {
        "base_url": BASE_URL,
        "product_name": PRODUCT_NAME,
        "instance_name": INSTANCE_NAME,
    }
    context.update(kwargs)
    return context

# ---------------------------------------------------------------------------
# 数据读写
# ---------------------------------------------------------------------------

INDEX_FILE = DATA_DIR / "index.json"
VISITORS_FILE = DATA_DIR / "visitors.json"
USERS_FILE = DATA_DIR / "users.json"
DEFAULT_USERNAME = "user"
USERNAME_MAX_LENGTH = 32
DEFAULT_PAGEGATE_NAME = "My PageGate"
RESERVED_ROUTE_SEGMENTS = {
    "api",
    "auth",
    "dashboard",
    "favicon.ico",
    "robots.txt",
}


def _normalize_page_owners(index: dict) -> bool:
    changed = False
    for page in index.get("pages", []):
        if not page.get("owner"):
            # Legacy pages predate multi-owner support; treat them as explicit
            # super-admin-owned pages until someone reassigns them.
            page["owner"] = SUPER_ADMIN_EMAIL
            changed = True
    return changed


def _normalize_visitor_record(visitor: dict) -> bool:
    changed = False

    for key in ("approved_pages", "pending_pages"):
        value = visitor.get(key)
        if not isinstance(value, list):
            visitor[key] = []
            changed = True

    if not isinstance(visitor.get("blocked"), bool):
        visitor["blocked"] = bool(visitor.get("blocked"))
        changed = True

    raw_owners = visitor.get("whitelisted_owners")
    if not isinstance(raw_owners, list):
        visitor["whitelisted_owners"] = []
        changed = True
    else:
        normalized_owners = []
        for owner in raw_owners:
            if not isinstance(owner, str):
                changed = True
                continue
            normalized = owner.strip().lower()
            if not normalized:
                changed = True
                continue
            if normalized not in normalized_owners:
                normalized_owners.append(normalized)
            elif owner != normalized:
                changed = True
        if normalized_owners != raw_owners:
            visitor["whitelisted_owners"] = normalized_owners
            changed = True

    return changed


def _normalize_visitors(data: dict) -> bool:
    changed = False
    visitors = data.get("visitors")
    if not isinstance(visitors, list):
        data["visitors"] = []
        return True

    for visitor in visitors:
        if isinstance(visitor, dict) and _normalize_visitor_record(visitor):
            changed = True
    return changed


def read_index() -> dict:
    if INDEX_FILE.exists():
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        if _normalize_page_owners(data):
            write_index(data)
        return data
    return {"pages": []}


def write_index(data: dict):
    INDEX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_visitors() -> dict:
    if VISITORS_FILE.exists():
        data = json.loads(VISITORS_FILE.read_text(encoding="utf-8"))
        if _normalize_visitors(data):
            write_visitors(data)
        return data
    return {"visitors": []}


def write_visitors(data: dict):
    VISITORS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_username_candidate(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    normalized = normalized[:USERNAME_MAX_LENGTH].strip("-")
    return normalized or DEFAULT_USERNAME


def _username_seed_from_pagegate_name(pagegate_name: str) -> str:
    cleaned = str(pagegate_name or "").strip()
    cleaned = re.sub(r"[’']s\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bpage\s*gate\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bpagegate\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_")
    return _normalize_username_candidate(cleaned or pagegate_name)


def _default_pagegate_name(username: str) -> str:
    base = (username or DEFAULT_USERNAME).strip("-_ ") or DEFAULT_USERNAME
    if base.lower() == DEFAULT_USERNAME:
        return DEFAULT_PAGEGATE_NAME
    return f"{base}'s PageGate"


def _normalize_pagegate_name(value: str, *, fallback_username: str = "") -> str:
    normalized = str(value or "").strip()
    if normalized:
        return normalized
    return _default_pagegate_name(fallback_username)


def _build_generated_email(*, username: str, existing_emails: set[str]) -> str:
    base = _normalize_username_candidate(username)
    suffix = 0
    while True:
        suffix_text = f"-{suffix}" if suffix else ""
        candidate = f"{base}{suffix_text}@pagegate.local"
        if candidate not in existing_emails:
            return candidate
        suffix += 1


def _build_unique_username(base: str, used: set[str]) -> str:
    candidate = _normalize_username_candidate(base)
    if candidate not in used:
        return candidate

    suffix = 2
    while True:
        suffix_text = f"-{suffix}"
        prefix = candidate[:USERNAME_MAX_LENGTH - len(suffix_text)].rstrip("-")
        unique = f"{prefix or DEFAULT_USERNAME}{suffix_text}"
        if unique not in used:
            return unique
        suffix += 1


def _reserved_username_segments() -> set[str]:
    reserved = {segment.lower() for segment in RESERVED_ROUTE_SEGMENTS}
    if INDEX_FILE.exists():
        index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        for page in index.get("pages", []):
            slug = str(page.get("slug", "")).strip().lower()
            if slug:
                reserved.add(slug)
    return reserved


def _normalize_user_record(user: dict, *, used_usernames: set[str], reserved_usernames: set[str]) -> bool:
    changed = False

    email = str(user.get("email", "")).strip().lower()
    if user.get("email") != email:
        user["email"] = email
        changed = True

    raw_username = str(user.get("username", "")).strip().lower()
    base = raw_username or (email.split("@")[0] if email else DEFAULT_USERNAME)
    normalized_username = _normalize_username_candidate(base)
    final_username = normalized_username
    disallowed = used_usernames | reserved_usernames
    if final_username in disallowed:
        final_username = _build_unique_username(normalized_username, disallowed)

    if user.get("username") != final_username:
        user["username"] = final_username
        changed = True

    pagegate_name = _normalize_pagegate_name(
        user.get("pagegate_name", ""),
        fallback_username=final_username,
    )
    if user.get("pagegate_name") != pagegate_name:
        user["pagegate_name"] = pagegate_name
        changed = True

    used_usernames.add(final_username)
    return changed


def _normalize_users(data: dict) -> bool:
    changed = False
    users = data.get("users")
    if not isinstance(users, list):
        data["users"] = []
        return True

    used_usernames: set[str] = set()
    reserved_usernames = _reserved_username_segments()
    for user in users:
        if isinstance(user, dict) and _normalize_user_record(
            user,
            used_usernames=used_usernames,
            reserved_usernames=reserved_usernames,
        ):
            changed = True
    return changed


def read_users() -> dict:
    if USERS_FILE.exists():
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        if _normalize_users(data):
            write_users(data)
        return data
    return {"users": []}


def write_users(data: dict):
    USERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_user_by_token(token: str) -> Optional[dict]:
    """通过 token 查找用户"""
    users_data = read_users()
    for u in users_data["users"]:
        if u["token"] == token:
            return u
    return None


def find_user_by_email(email: str) -> Optional[dict]:
    """通过邮箱查找用户"""
    users_data = read_users()
    for u in users_data["users"]:
        if u["email"] == email:
            return u
    return None


def find_user_by_username(username: str) -> Optional[dict]:
    normalized = _normalize_username_candidate(username)
    users_data = read_users()
    for u in users_data["users"]:
        if u.get("username") == normalized:
            return u
    return None


def find_page(slug: str) -> Optional[dict]:
    index = read_index()
    for page in index["pages"]:
        if page["slug"] == slug:
            return page
    return None


def find_visitor(visitor_id: str) -> Optional[dict]:
    visitors = read_visitors()
    for v in visitors["visitors"]:
        if v["id"] == visitor_id:
            return v
    return None


# ---------------------------------------------------------------------------
# Session 管理
# ---------------------------------------------------------------------------

SESSION_COOKIE = "pagegate_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 天


def get_session_visitor(request: Request) -> Optional[dict]:
    """从 Cookie 中解析当前登录的访客"""
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    try:
        visitor_id = signer.unsign(cookie, max_age=SESSION_MAX_AGE).decode()
        return find_visitor(visitor_id)
    except BadSignature:
        return None


def set_session_cookie(response: Response, visitor_id: str):
    signed = signer.sign(visitor_id).decode()
    response.set_cookie(
        SESSION_COOKIE,
        signed,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


# ---------------------------------------------------------------------------
# OpenClaw 通知推送
# ---------------------------------------------------------------------------


def build_access_event(page: dict, visitor: dict) -> dict:
    provider_name = {"dingtalk": "钉钉", "wechat": "微信"}.get(
        visitor.get("provider", ""), visitor.get("provider", "未知")
    )
    event = {
        "id": f"req_{int(time.time() * 1000)}_{secrets.token_hex(4)}",
        "type": "access_requested",
        "page": {
            "slug": page["slug"],
            "title": page["title"],
        },
        "visitor": {
            "id": visitor["id"],
            "name": visitor["name"],
            "provider": visitor.get("provider", ""),
            "provider_name": provider_name,
        },
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "message": (
            f"有人想查看你的页面\n"
            f"页面：{page['title']} ({page['slug']})\n"
            f"访客：{visitor['name']}（{provider_name}登录）\n"
            f"访客ID：{visitor['id']}\n\n要通过吗？"
        ),
    }
    return event


async def publish_event(event: dict):
    EVENT_HISTORY.append(event)
    if len(EVENT_HISTORY) > EVENT_HISTORY_LIMIT:
        del EVENT_HISTORY[:-EVENT_HISTORY_LIMIT]

    stale = []
    for queue, subscriber in list(EVENT_SUBSCRIBERS.items()):
        if not _event_belongs_to_subscriber(event, subscriber):
            continue
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            subscriber["disconnect"] = True
        except Exception:
            stale.append(queue)
    for queue in stale:
        EVENT_SUBSCRIBERS.pop(queue, None)


def _approval_key(slug: str, visitor_id: str) -> str:
    return f"{slug}:{visitor_id}"


def _sse_control_frame(comment: str = "connected") -> str:
    """发送首帧控制信息，避免客户端在空闲连接上读超时。"""
    return f"retry: {SSE_RETRY_MS}\n: {comment}\n\n"


def _sse_ping_frame() -> str:
    return ": ping\n\n"


async def publish_approval_event(slug: str, visitor_id: str, status: str):
    key = _approval_key(slug, visitor_id)
    payload = {
        "slug": slug,
        "visitor_id": visitor_id,
        "status": status,
        "published_at": time.time(),
    }
    queues = list(APPROVAL_SUBSCRIBERS.get(key, set()))
    stale = []
    for queue in queues:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            stale.append(queue)
        except Exception:
            stale.append(queue)
    if stale and key in APPROVAL_SUBSCRIBERS:
        for queue in stale:
            APPROVAL_SUBSCRIBERS[key].discard(queue)
        if not APPROVAL_SUBSCRIBERS[key]:
            APPROVAL_SUBSCRIBERS.pop(key, None)


async def notify_openclaw(page: dict, visitor: dict):
    """通过 OpenClaw webhook 推送，或通过 SSE 广播事件"""
    event = build_access_event(page, visitor)
    await publish_event(event)

    if not OPENCLAW_WEBHOOK_URL:
        logger.info("OpenClaw webhook 未配置，已仅广播到 SSE 订阅者")
        return

    payload = {
        "message": event["message"],
        "event": event,
        "name": "pagegate-client",
        "deliver": True,
    }

    headers = {}
    if OPENCLAW_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_WEBHOOK_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                OPENCLAW_WEBHOOK_URL, json=payload, headers=headers
            )
            if resp.status_code < 300:
                logger.info(f"OpenClaw 通知已推送：{visitor['name']} → {page['slug']}")
            else:
                logger.warning(f"OpenClaw 通知推送失败: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.warning(f"OpenClaw 通知推送异常: {e}")


# ---------------------------------------------------------------------------
# Admin 鉴权
# ---------------------------------------------------------------------------

# 超级管理员虚拟用户（用于向后兼容 config.yaml 中的 admin_token）
SUPER_ADMIN_USER = {
    "email": SUPER_ADMIN_EMAIL,
    "token": SUPER_ADMIN_TOKEN,
    "role": "super_admin",
    "created_at": "",
}


def _extract_token(request: Request) -> str:
    """从 Authorization header 或 query param 中提取 token"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.query_params.get("token", "")


def verify_admin(request: Request) -> dict:
    """验证管理员身份，返回用户 dict"""
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # 超级管理员 token（向后兼容）
    if SUPER_ADMIN_TOKEN and token == SUPER_ADMIN_TOKEN:
        return SUPER_ADMIN_USER

    # 通过 token 查找注册用户
    user = find_user_by_token(token)
    if user:
        return user

    raise HTTPException(status_code=401, detail="Unauthorized")


def _is_super_admin(user: dict) -> bool:
    return user.get("role") == "super_admin"


def _page_owner(page: dict) -> str:
    return page.get("owner") or SUPER_ADMIN_EMAIL


def _page_owner_user(page: dict) -> Optional[dict]:
    owner_email = _page_owner(page)
    if owner_email == SUPER_ADMIN_EMAIL:
        return None
    return find_user_by_email(owner_email)


def _page_owner_username(page: dict) -> str:
    owner_user = _page_owner_user(page)
    if not owner_user:
        return ""
    return owner_user.get("username", "")


def _find_page_for_owner_slug(owner_email: str, slug: str) -> Optional[dict]:
    page = find_page(slug)
    if page and _page_owner(page) == owner_email:
        return page
    return None


def _find_page_for_username_slug(username: str, slug: str) -> Optional[dict]:
    owner = find_user_by_username(username)
    if not owner:
        return None
    return _find_page_for_owner_slug(owner["email"], slug)


def _page_path(page: dict) -> str:
    owner_username = _page_owner_username(page)
    if owner_username:
        return f"/{owner_username}/{page['slug']}/"
    return f"/{page['slug']}"


def _page_redirect_target(page: dict) -> str:
    return _page_path(page).lstrip("/")


def _page_url(page: dict) -> str:
    return f"{BASE_URL}{_page_path(page)}"


def _owner_home_path(user: dict) -> str:
    if _is_super_admin(user) or not user.get("username"):
        return "/"
    return f"/{user['username']}"


def _owner_home_url(user: dict) -> str:
    return f"{BASE_URL}{_owner_home_path(user)}"


def _dashboard_url(user: dict, *, token: str = "") -> str:
    resolved_token = token or user.get("token", "")
    if not resolved_token:
        return f"{BASE_URL}/dashboard"
    return f"{BASE_URL}/dashboard?{urlencode({'token': resolved_token})}"


def _pagegate_name_for_user(user: dict) -> str:
    if _is_super_admin(user):
        return INSTANCE_NAME
    return _normalize_pagegate_name(
        user.get("pagegate_name", ""),
        fallback_username=user.get("username", ""),
    )


def _account_payload(user: dict, *, token: str = "") -> dict:
    return {
        "email": user.get("email", ""),
        "role": user.get("role", ""),
        "username": user.get("username", ""),
        "pagegate_name": _pagegate_name_for_user(user),
        "pagegate_url": _owner_home_url(user),
        "dashboard_url": _dashboard_url(user, token=token),
    }


def _validate_requested_username(username: str, *, exclude_email: str = "") -> str:
    normalized = _normalize_username_candidate(username)
    reserved = _reserved_username_segments()
    existing = find_user_by_username(normalized)
    if existing and existing["email"] != exclude_email:
        raise HTTPException(409, "Username already registered")
    if normalized in reserved:
        current = find_user_by_email(exclude_email) if exclude_email else None
        if not current or current.get("username") != normalized:
            raise HTTPException(409, "Username conflicts with an existing page or route")
    return normalized


def _validate_slug_or_raise(slug: str):
    normalized = slug.strip()
    if not normalized:
        raise HTTPException(400, "slug required")
    if "/" in normalized:
        raise HTTPException(400, "slug cannot contain /")
    if normalized.lower() in RESERVED_ROUTE_SEGMENTS:
        raise HTTPException(409, "Slug conflicts with a reserved route")
    if find_user_by_username(normalized):
        raise HTTPException(409, "Slug conflicts with an existing username")
    return normalized


def _resolve_owner_email(owner: str) -> str:
    normalized = owner.strip().lower()
    if not normalized:
        raise HTTPException(400, "owner required")
    if normalized == SUPER_ADMIN_EMAIL:
        return SUPER_ADMIN_EMAIL
    target = find_user_by_email(normalized)
    if not target:
        raise HTTPException(
            400,
            f"owner must be an existing user email or {SUPER_ADMIN_EMAIL}",
        )
    return target["email"]


def _user_owns_page(user: dict, page: dict) -> bool:
    """检查用户是否拥有该页面（超级管理员拥有所有页面）"""
    if _is_super_admin(user):
        return True
    return _page_owner(page) == user["email"]


def _owner_scope_for_user(user: dict) -> str:
    if _is_super_admin(user):
        return SUPER_ADMIN_EMAIL
    return user["email"].strip().lower()


def _get_pages_for_owner_scope(owner_scope: str, index: dict) -> list:
    return [p for p in index["pages"] if _page_owner(p) == owner_scope]


def _visitor_has_owner_whitelist(visitor: dict, owner_scope: str) -> bool:
    return owner_scope in visitor.get("whitelisted_owners", [])


def _get_user_pages(user: dict, index: dict) -> list:
    """获取用户拥有的所有页面"""
    if _is_super_admin(user):
        return index["pages"]
    return [p for p in index["pages"] if _page_owner(p) == user["email"]]


def _require_page_owner(user: dict, page: dict):
    """要求用户是页面的所有者，否则 403"""
    if not _user_owns_page(user, page):
        raise HTTPException(status_code=403, detail="You don't own this page")


def _page_template_model(page: dict) -> dict:
    item = dict(page)
    item["public_path"] = _page_path(page)
    item["public_url"] = _page_url(page)
    item["owner_username"] = _page_owner_username(page)
    return item


def _public_pages_for_index(index: dict) -> list[dict]:
    public_pages = [
        _page_template_model(page)
        for page in index["pages"]
        if page.get("access") == "public"
    ]
    public_pages.sort(key=lambda page: page.get("created_at", ""), reverse=True)
    return public_pages


def _owner_gateway_models(index: dict) -> list[dict]:
    users_data = read_users()
    users_by_email = {user["email"]: user for user in users_data["users"]}
    gateways_by_owner: dict[str, dict] = {}

    for page in index["pages"]:
        if page.get("access") != "public":
            continue
        owner_email = _page_owner(page)
        owner_user = users_by_email.get(owner_email)
        if not owner_user:
            continue

        latest_at = page.get("updated_at") or page.get("created_at") or ""
        gateway = gateways_by_owner.setdefault(owner_email, {
            "username": owner_user["username"],
            "home_path": _owner_home_path(owner_user),
            "home_url": _owner_home_url(owner_user),
            "page_count": 0,
            "latest_at": "",
        })
        gateway["page_count"] += 1
        if latest_at > gateway["latest_at"]:
            gateway["latest_at"] = latest_at

    gateways = list(gateways_by_owner.values())
    gateways.sort(key=lambda gateway: (gateway.get("latest_at", ""), gateway["username"]), reverse=True)
    return gateways


def _sort_page_models_desc(pages: list[dict]):
    pages.sort(
        key=lambda page: (
            page.get("updated_at") or page.get("created_at") or "",
            page.get("created_at") or "",
            page.get("slug", ""),
        ),
        reverse=True,
    )


def _owner_home_context(owner: dict, request: Request) -> dict:
    index = read_index()
    owner_scope = owner["email"]
    owner_pages = _get_pages_for_owner_scope(owner_scope, index)
    public_pages: list[dict] = []
    authorized_pages: list[dict] = []
    pending_pages: list[dict] = []

    viewer = get_session_visitor(request)
    viewer_logged_in = bool(viewer)
    viewer_blocked = bool(viewer and viewer.get("blocked"))
    owner_whitelisted = bool(
        viewer and not viewer_blocked and _visitor_has_owner_whitelist(viewer, owner_scope)
    )
    approved_slugs = set(viewer.get("approved_pages", [])) if viewer and not viewer_blocked else set()
    pending_slugs = set(viewer.get("pending_pages", [])) if viewer and not viewer_blocked else set()

    for page in owner_pages:
        model = _page_template_model(page)
        access = page.get("access", "public")
        if access == "public":
            public_pages.append(model)
            continue
        if not viewer_logged_in or viewer_blocked:
            continue
        if owner_whitelisted or page["slug"] in approved_slugs:
            authorized_pages.append(model)
            continue
        if access == "approval" and page["slug"] in pending_slugs:
            pending_pages.append(model)

    _sort_page_models_desc(public_pages)
    _sort_page_models_desc(authorized_pages)
    _sort_page_models_desc(pending_pages)

    user_agent = get_user_agent(request)
    redirect_target = owner.get("username", "")
    default_tab = "public"
    if viewer_logged_in and (authorized_pages or pending_pages):
        default_tab = "authorized"
    elif (authorized_pages or pending_pages) and not public_pages:
        default_tab = "authorized"

    return {
        "owner": owner,
        "owner_home_url": _owner_home_url(owner),
        "public_pages": public_pages,
        "authorized_pages": authorized_pages,
        "pending_pages": pending_pages,
        "viewer": viewer,
        "viewer_logged_in": viewer_logged_in,
        "viewer_blocked": viewer_blocked,
        "owner_whitelisted": owner_whitelisted,
        "default_tab": default_tab,
        "has_dingtalk": bool(CONFIG.get("dingtalk", {}).get("app_key")),
        "has_wechat": bool(CONFIG.get("wechat", {}).get("app_id")),
        "is_mobile": is_mobile_user_agent(user_agent),
        "is_dingtalk": is_dingtalk_user_agent(user_agent),
        **(_build_login_urls(redirect_target) if redirect_target else {}),
    }


# ---------------------------------------------------------------------------
# 目录页生成
# ---------------------------------------------------------------------------


def regenerate_index_page():
    """重新生成公开目录页"""
    index = read_index()
    public_pages = _public_pages_for_index(index)
    owner_gateways = _owner_gateway_models(index)
    html = templates.get_template("index.html").render(
        **template_context(
            pages=public_pages,
            owner_gateways=owner_gateways,
        )
    )
    (PAGES_DIR / "index.html").write_text(html, encoding="utf-8")


@app.on_event("startup")
async def startup_regenerate_index_page():
    """Keep the generated homepage aligned with config and template changes."""
    regenerate_index_page()


# ---------------------------------------------------------------------------
# 路由：公开目录
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def home():
    index_file = PAGES_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    # 首次启动，还没有页面
    regenerate_index_page()
    return HTMLResponse((PAGES_DIR / "index.html").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 用户注册 / 登录
# ---------------------------------------------------------------------------


@app.post("/api/auth/register")
async def register(request: Request):
    """注册新用户，返回 token"""
    if REGISTRATION_MODE != "open":
        raise HTTPException(status_code=403, detail="Registration is closed")

    body = await request.json()
    email = str(body.get("email", "") or "").strip().lower()
    password = str(body.get("password", "") or "")
    requested_username = str(body.get("username", "") or "").strip()
    pagegate_name = str(body.get("pagegate_name", "") or "").strip()
    quick_register = not email and not password

    users_data = read_users()
    used_usernames = {
        user.get("username", "")
        for user in users_data["users"]
        if isinstance(user, dict)
    } | _reserved_username_segments()
    existing_emails = {
        str(user.get("email", "")).strip().lower()
        for user in users_data["users"]
        if isinstance(user, dict)
    }

    if quick_register:
        if not pagegate_name:
            raise HTTPException(400, "pagegate_name required")
        if requested_username:
            username = _validate_requested_username(requested_username)
        else:
            username = _build_unique_username(
                _username_seed_from_pagegate_name(pagegate_name),
                used_usernames,
            )
        email = _build_generated_email(username=username, existing_emails=existing_emails)
        password = secrets.token_urlsafe(24)
    else:
        if not email or not password:
            raise HTTPException(400, "email and password required")
        if len(password) < 6:
            raise HTTPException(400, "password must be at least 6 characters")
        if email in existing_emails:
            raise HTTPException(409, "Email already registered")

        if requested_username:
            username = _validate_requested_username(requested_username)
        else:
            username_seed = email.split("@")[0]
            if pagegate_name:
                username_seed = _username_seed_from_pagegate_name(pagegate_name)
            username = _build_unique_username(username_seed, used_usernames)

    # 生成 token 和密码哈希
    token = f"uhub_{secrets.token_hex(24)}"
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    final_pagegate_name = _normalize_pagegate_name(
        pagegate_name,
        fallback_username=username,
    )

    user = {
        "email": email,
        "username": username,
        "pagegate_name": final_pagegate_name,
        "password_hash": password_hash,
        "token": token,
        "role": "admin",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    users_data["users"].append(user)
    write_users(users_data)

    logger.info(
        f"新用户注册: {email} ({username})"
        + (" [quick]" if quick_register else "")
    )
    return {
        "ok": True,
        "token": token,
        **_account_payload(user, token=token),
        "quick_registered": quick_register,
    }


@app.post("/api/auth/login")
async def login(request: Request):
    """登录获取 token"""
    body = await request.json()
    email = str(body.get("email", "") or "").strip().lower()
    password = str(body.get("password", "") or "")

    if not email or not password:
        raise HTTPException(400, "email and password required")

    user = find_user_by_email(email)
    if not user:
        raise HTTPException(401, "Invalid email or password")

    password_hash = str(user.get("password_hash", "")).strip()
    if not password_hash:
        raise HTTPException(401, "Invalid email or password")

    try:
        valid = bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(401, "Invalid email or password")

    return {
        "ok": True,
        "token": user["token"],
        **_account_payload(user, token=user["token"]),
    }


@app.get("/api/me")
async def account_profile(request: Request, user=Depends(verify_admin)):
    return {
        "ok": True,
        **_account_payload(user, token=_extract_token(request)),
    }


# ---------------------------------------------------------------------------
# 路由：管理后台
# ---------------------------------------------------------------------------


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(verify_admin)):
    index = read_index()
    visitors_data = read_visitors()
    provider_names = {"dingtalk": "钉钉", "wechat": "微信"}

    # 只显示当前用户拥有的页面
    my_pages = [_page_template_model(page) for page in _get_user_pages(user, index)]
    owner_scope = _owner_scope_for_user(user)
    owner_slugs = {page["slug"] for page in my_pages}

    # 按分类分组
    categories: dict[str, list] = {}
    for page in my_pages:
        cat = page.get("category", "未分类")
        categories.setdefault(cat, []).append(page)

    # 为每个页面附加访客信息
    for page in my_pages:
        slug = page["slug"]
        approved = [
            v for v in visitors_data["visitors"]
            if slug in v.get("approved_pages", [])
        ]
        pending = [
            v for v in visitors_data["visitors"]
            if slug in v.get("pending_pages", [])
        ]
        page["_approved_visitors"] = approved
        page["_pending_visitors"] = pending

    owner_visitors = []
    for visitor in visitors_data["visitors"]:
        item = _serialize_owner_visitor(
            visitor,
            owner_scope=owner_scope,
            owner_slugs=owner_slugs,
            provider_names=provider_names,
        )
        if item:
            owner_visitors.append(item)

    owner_visitors.sort(
        key=lambda item: (
            0 if item["whitelisted"] else 1,
            -len(item["requested_pages"]),
            item.get("first_seen", ""),
            item["id"],
        )
    )

    return templates.TemplateResponse(
        "dashboard.html",
        template_context(
            request=request,
            categories=categories,
            api_token=user["token"],
            owner_visitors=owner_visitors,
            owner_home_url=_owner_home_url(user),
            owner_username=user.get("username", ""),
        ),
    )


# ---------------------------------------------------------------------------
# API：发布
# ---------------------------------------------------------------------------


@app.post("/api/publish")
async def publish(
    request: Request,
    slug: str = Form(...),
    title: str = Form(...),
    category: str = Form("未分类"),
    access: str = Form("public"),
    description: str = Form(""),
    file: UploadFile = File(...),
    user=Depends(verify_admin),
):
    if access not in ("public", "approval", "private"):
        raise HTTPException(400, "access must be public, approval, or private")
    slug = slug.strip()

    # 更新索引
    index = read_index()
    # 如果已存在则更新（必须是自己的页面）
    existing = None
    for p in index["pages"]:
        if p["slug"] == slug:
            existing = p
            break

    now = datetime.now(timezone.utc).isoformat()
    if existing:
        _require_page_owner(user, existing)
        page_record = existing
    else:
        slug = _validate_slug_or_raise(slug)
        page_dir = PAGES_DIR / slug
        if page_dir.exists():
            raise HTTPException(
                409,
                "Page directory already exists; reindex and assign an owner before publishing",
            )

    # 权限校验完成后再写 HTML，避免跨 owner 覆盖现有页面文件。
    page_dir = PAGES_DIR / slug
    page_dir.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    (page_dir / "index.html").write_bytes(content)

    if existing:
        existing["title"] = title
        existing["category"] = category
        existing["access"] = access
        existing["description"] = description
        existing["updated_at"] = now
    else:
        page_record = {
            "slug": slug,
            "title": title,
            "category": category,
            "access": access,
            "description": description,
            "owner": user["email"],
            "created_at": now,
            "updated_at": now,
        }
        index["pages"].append(page_record)

    write_index(index)
    regenerate_index_page()

    return {"ok": True, "url": _page_url(page_record)}


@app.post("/api/reindex")
async def reindex(user=Depends(verify_admin)):
    """扫描 pages 目录，重建索引（新发现的页面归当前用户所有）"""
    index = read_index()
    existing_slugs = {p["slug"] for p in index["pages"]}
    now = datetime.now(timezone.utc).isoformat()

    for entry in PAGES_DIR.iterdir():
        if entry.is_dir() and (entry / "index.html").exists():
            slug = entry.name
            if slug not in existing_slugs:
                index["pages"].append({
                    "slug": slug,
                    "title": slug,
                    "category": "未分类",
                    "access": "public",
                    "description": "",
                    "owner": SUPER_ADMIN_EMAIL,
                    "created_at": now,
                    "updated_at": now,
                })

    write_index(index)
    regenerate_index_page()
    return {"ok": True, "pages": len(index["pages"])}


# ---------------------------------------------------------------------------
# API：页面管理
# ---------------------------------------------------------------------------


@app.put("/api/pages/{slug}")
async def update_page(slug: str, request: Request, user=Depends(verify_admin)):
    body = await request.json()
    index = read_index()
    page = None
    for p in index["pages"]:
        if p["slug"] == slug:
            page = p
            break
    if not page:
        raise HTTPException(404, "Page not found")

    _require_page_owner(user, page)

    for key in ("title", "category", "access", "description"):
        if key in body:
            page[key] = body[key]
    if "owner" in body:
        if not _is_super_admin(user):
            raise HTTPException(403, "Only super admin can change owner")
        page["owner"] = _resolve_owner_email(body["owner"])
    page["updated_at"] = datetime.now(timezone.utc).isoformat()

    write_index(index)
    regenerate_index_page()
    return {"ok": True}


@app.delete("/api/pages/{slug}")
async def delete_page(slug: str, user=Depends(verify_admin)):
    index = read_index()
    page = None
    for p in index["pages"]:
        if p["slug"] == slug:
            page = p
            break
    if not page:
        raise HTTPException(404, "Page not found")

    _require_page_owner(user, page)

    index["pages"] = [p for p in index["pages"] if p["slug"] != slug]
    write_index(index)

    page_dir = PAGES_DIR / slug
    if page_dir.exists():
        shutil.rmtree(page_dir)

    regenerate_index_page()
    return {"ok": True}


# ---------------------------------------------------------------------------
# API：访客管理
# ---------------------------------------------------------------------------


def _find_page_or_404(slug: str, user: dict) -> dict:
    """查找页面并验证所有权，否则 404/403"""
    page = find_page(slug)
    if not page:
        raise HTTPException(404, "Page not found")
    _require_page_owner(user, page)
    return page


def _serialize_owner_visitor(
    visitor: dict,
    *,
    owner_scope: str,
    owner_slugs: set[str],
    provider_names: dict[str, str],
) -> Optional[dict]:
    approved_pages = [
        slug for slug in visitor.get("approved_pages", [])
        if slug in owner_slugs
    ]
    pending_pages = [
        slug for slug in visitor.get("pending_pages", [])
        if slug in owner_slugs
    ]
    requested_pages = []
    for slug in approved_pages + pending_pages:
        if slug not in requested_pages:
            requested_pages.append(slug)

    whitelisted = _visitor_has_owner_whitelist(visitor, owner_scope)
    if not requested_pages and not whitelisted:
        return None

    return {
        "id": visitor["id"],
        "name": visitor.get("name", ""),
        "provider": provider_names.get(visitor.get("provider", ""), visitor.get("provider", "")),
        "provider_code": visitor.get("provider", ""),
        "avatar": visitor.get("avatar", ""),
        "first_seen": visitor.get("first_seen", ""),
        "blocked": bool(visitor.get("blocked")),
        "whitelisted": whitelisted,
        "requested_pages": requested_pages,
        "approved_pages": approved_pages,
        "pending_pages": pending_pages,
    }


@app.get("/api/pages/{slug}/visitors")
async def get_visitors(slug: str, user=Depends(verify_admin)):
    _find_page_or_404(slug, user)
    visitors_data = read_visitors()
    approved = [
        v for v in visitors_data["visitors"]
        if slug in v.get("approved_pages", [])
    ]
    pending = [
        v for v in visitors_data["visitors"]
        if slug in v.get("pending_pages", [])
    ]
    return {"approved": approved, "pending": pending}


@app.get("/api/visitors")
async def list_owner_visitors(user=Depends(verify_admin)):
    visitors_data = read_visitors()
    index = read_index()
    owner_scope = _owner_scope_for_user(user)
    owner_pages = _get_pages_for_owner_scope(owner_scope, index)
    owner_slugs = {page["slug"] for page in owner_pages}
    provider_names = {"dingtalk": "钉钉", "wechat": "微信"}

    visitors = []
    for visitor in visitors_data["visitors"]:
        item = _serialize_owner_visitor(
            visitor,
            owner_scope=owner_scope,
            owner_slugs=owner_slugs,
            provider_names=provider_names,
        )
        if item:
            visitors.append(item)

    visitors.sort(
        key=lambda item: (
            0 if item["whitelisted"] else 1,
            -len(item["requested_pages"]),
            item.get("first_seen", ""),
            item["id"],
        )
    )
    return {"visitors": visitors, "count": len(visitors)}


@app.post("/api/visitors/{visitor_id}/whitelist")
async def add_owner_whitelist(visitor_id: str, user=Depends(verify_admin)):
    visitors_data = read_visitors()
    index = read_index()
    owner_scope = _owner_scope_for_user(user)
    owner_slugs = {
        page["slug"]
        for page in _get_pages_for_owner_scope(owner_scope, index)
    }

    for visitor in visitors_data["visitors"]:
        if visitor["id"] != visitor_id:
            continue

        whitelist = visitor.setdefault("whitelisted_owners", [])
        if owner_scope not in whitelist:
            whitelist.append(owner_scope)

        pending_pages = visitor.get("pending_pages", [])
        cleared_pending = [slug for slug in pending_pages if slug in owner_slugs]
        if cleared_pending:
            visitor["pending_pages"] = [
                slug for slug in pending_pages if slug not in owner_slugs
            ]

        write_visitors(visitors_data)
        for slug in cleared_pending:
            await publish_approval_event(slug, visitor_id, "approved")

        return {
            "ok": True,
            "visitor_id": visitor_id,
            "whitelisted": True,
            "cleared_pending_pages": cleared_pending,
        }

    raise HTTPException(404, "Visitor not found")


@app.delete("/api/visitors/{visitor_id}/whitelist")
async def remove_owner_whitelist(visitor_id: str, user=Depends(verify_admin)):
    visitors_data = read_visitors()
    owner_scope = _owner_scope_for_user(user)

    for visitor in visitors_data["visitors"]:
        if visitor["id"] != visitor_id:
            continue

        whitelist = visitor.setdefault("whitelisted_owners", [])
        visitor["whitelisted_owners"] = [
            owner for owner in whitelist if owner != owner_scope
        ]
        write_visitors(visitors_data)
        return {
            "ok": True,
            "visitor_id": visitor_id,
            "whitelisted": False,
        }

    raise HTTPException(404, "Visitor not found")


@app.post("/api/pages/{slug}/approve")
async def approve_visitor(slug: str, request: Request, user=Depends(verify_admin)):
    _find_page_or_404(slug, user)
    body = await request.json()
    visitor_id = body.get("visitor_id")
    if not visitor_id:
        raise HTTPException(400, "visitor_id required")

    visitors_data = read_visitors()
    for v in visitors_data["visitors"]:
        if v["id"] == visitor_id:
            if slug in v.get("pending_pages", []):
                v["pending_pages"].remove(slug)
            if slug not in v.get("approved_pages", []):
                v.setdefault("approved_pages", []).append(slug)
            break
    else:
        raise HTTPException(404, "Visitor not found")

    write_visitors(visitors_data)
    await publish_approval_event(slug, visitor_id, "approved")
    return {"ok": True}


@app.post("/api/pages/{slug}/reject")
async def reject_visitor(slug: str, request: Request, user=Depends(verify_admin)):
    _find_page_or_404(slug, user)
    body = await request.json()
    visitor_id = body.get("visitor_id")
    if not visitor_id:
        raise HTTPException(400, "visitor_id required")

    visitors_data = read_visitors()
    for v in visitors_data["visitors"]:
        if v["id"] == visitor_id:
            if slug in v.get("pending_pages", []):
                v["pending_pages"].remove(slug)
            break

    write_visitors(visitors_data)
    await publish_approval_event(slug, visitor_id, "rejected")
    return {"ok": True}


@app.delete("/api/pages/{slug}/visitors/{visitor_id}")
async def revoke_visitor(slug: str, visitor_id: str, user=Depends(verify_admin)):
    _find_page_or_404(slug, user)
    visitors_data = read_visitors()
    for v in visitors_data["visitors"]:
        if v["id"] == visitor_id:
            if slug in v.get("approved_pages", []):
                v["approved_pages"].remove(slug)
            break

    write_visitors(visitors_data)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 页面 URL / 鉴权渲染
# ---------------------------------------------------------------------------


def _find_page_for_redirect_target(redirect_target: str) -> Optional[dict]:
    normalized = redirect_target.strip("/")
    if not normalized:
        return None
    parts = normalized.split("/")
    if len(parts) == 1:
        return find_page(parts[0])
    if len(parts) == 2:
        username, slug = parts
        return _find_page_for_username_slug(username, slug)
    return None


def _build_login_urls(redirect_target: str) -> dict:
    return {
        "dingtalk_login_url": f"{BASE_URL}/auth/dingtalk?{urlencode({'redirect': redirect_target})}",
        "dingtalk_mobile_login_url": f"{BASE_URL}/auth/dingtalk?{urlencode({'redirect': redirect_target, 'mobile': 1})}",
        "wechat_login_url": f"{BASE_URL}/auth/wechat?{urlencode({'redirect': redirect_target})}",
    }


def _render_login_page(request: Request, page: dict, *, redirect_target: str) -> HTMLResponse:
    has_dingtalk = bool(CONFIG.get("dingtalk", {}).get("app_key"))
    has_wechat = bool(CONFIG.get("wechat", {}).get("app_id"))
    user_agent = get_user_agent(request)
    is_mobile = is_mobile_user_agent(user_agent)
    is_dingtalk = is_dingtalk_user_agent(user_agent)
    return templates.TemplateResponse(
        "login.html",
        template_context(
            request=request,
            slug=page.get("slug", ""),
            title=page.get("title", page.get("slug", "")),
            has_dingtalk=has_dingtalk,
            has_wechat=has_wechat,
            is_mobile=is_mobile,
            is_dingtalk=is_dingtalk,
            redirect_target=redirect_target,
            **_build_login_urls(redirect_target),
        ),
    )


def _page_file_or_404(page: dict) -> Path:
    page_file = PAGES_DIR / page["slug"] / "index.html"
    if not page_file.exists():
        raise HTTPException(404, "Page file not found")
    return page_file


async def _render_page_access(page: dict, request: Request, *, redirect_target: str) -> Response:
    page_file = _page_file_or_404(page)
    access = page.get("access", "public")
    owner_scope = _page_owner(page)

    if access == "public":
        return HTMLResponse(page_file.read_text(encoding="utf-8"))

    if access == "private":
        token = _extract_token(request)
        if token:
            if SUPER_ADMIN_TOKEN and token == SUPER_ADMIN_TOKEN:
                return HTMLResponse(page_file.read_text(encoding="utf-8"))
            user = find_user_by_token(token)
            if user and _user_owns_page(user, page):
                return HTMLResponse(page_file.read_text(encoding="utf-8"))

        visitor = get_session_visitor(request)
        if not visitor:
            return _render_login_page(request, page, redirect_target=redirect_target)

        if visitor.get("blocked"):
            raise HTTPException(403, "You have been blocked")
        if _visitor_has_owner_whitelist(visitor, owner_scope):
            return HTMLResponse(page_file.read_text(encoding="utf-8"))
        raise HTTPException(403, "This page is private")

    visitor = get_session_visitor(request)
    if not visitor:
        return _render_login_page(request, page, redirect_target=redirect_target)

    if visitor.get("blocked"):
        raise HTTPException(403, "You have been blocked")

    if _visitor_has_owner_whitelist(visitor, owner_scope):
        return HTMLResponse(page_file.read_text(encoding="utf-8"))

    slug = page["slug"]
    if slug in visitor.get("approved_pages", []):
        return HTMLResponse(page_file.read_text(encoding="utf-8"))

    if slug not in visitor.get("pending_pages", []):
        visitors_data = read_visitors()
        for v in visitors_data["visitors"]:
            if v["id"] == visitor["id"]:
                v.setdefault("pending_pages", []).append(slug)
                break
        write_visitors(visitors_data)
        await notify_openclaw(page, visitor)

    return templates.TemplateResponse(
        "pending.html",
        template_context(
            request=request,
            page=page,
            slug=slug,
            visitor=visitor,
        ),
    )


# ---------------------------------------------------------------------------
# OAuth：钉钉
# ---------------------------------------------------------------------------


@app.get("/auth/dingtalk")
async def dingtalk_login(request: Request, redirect: str = Query(""), mobile: int = Query(0)):
    dt_config = CONFIG.get("dingtalk", {})
    if not dt_config.get("app_key") or not dt_config.get("app_secret"):
        raise HTTPException(status_code=500, detail="DingTalk OAuth not configured")

    callback_url = f"{BASE_URL}/auth/dingtalk/callback"
    state = f"{redirect}|{secrets.token_hex(8)}"
    auth_url = "https://login.dingtalk.com/oauth2/auth?" + urlencode({
        "redirect_uri": callback_url,
        "response_type": "code",
        "client_id": dt_config["app_key"],
        "scope": "openid",
        "state": state,
        "prompt": "consent",
    })

    user_agent = get_user_agent(request)
    is_mobile = bool(mobile) or is_mobile_user_agent(user_agent)
    is_dingtalk = is_dingtalk_user_agent(user_agent)

    if is_mobile:
        if is_dingtalk:
            return RedirectResponse(auth_url)
        return templates.TemplateResponse(
            "dingtalk_mobile_launch.html",
            template_context(
                request=request,
                auth_url=auth_url,
                dingtalk_app_url=build_dingtalk_mobile_launch_url(auth_url),
                redirect=redirect,
            ),
        )

    return RedirectResponse(auth_url)


@app.get("/auth/dingtalk/callback")
async def dingtalk_callback(
    request: Request,
    code: str = Query(""),
    state: str = Query(""),
):
    """钉钉 OAuth 回调"""
    dt_config = CONFIG.get("dingtalk", {})
    app_key = dt_config.get("app_key", "")
    app_secret = dt_config.get("app_secret", "")

    if not code:
        raise HTTPException(400, "Missing code")

    # 用 code 换取 user access token
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
            json={
                "clientId": app_key,
                "clientSecret": app_secret,
                "code": code,
                "grantType": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(502, "Failed to get DingTalk token")
        token_data = token_resp.json()
        access_token = token_data.get("accessToken", "")

        # 获取用户信息
        user_resp = await client.get(
            "https://api.dingtalk.com/v1.0/contact/users/me",
            headers={"x-acs-dingtalk-access-token": access_token},
        )
        if user_resp.status_code != 200:
            raise HTTPException(502, "Failed to get DingTalk user info")
        user_data = user_resp.json()

    visitor_id = f"dingtalk_{user_data.get('openId', '')}"
    name = user_data.get("nick", "钉钉用户")
    avatar = user_data.get("avatarUrl", "")

    # 解析 redirect 目标
    redirect_target = ""
    if state and "|" in state:
        redirect_target = state.split("|", 1)[0]

    # 注册或更新访客
    return await _register_visitor_and_redirect(
        request, visitor_id, "dingtalk", name, avatar, redirect_target
    )


# ---------------------------------------------------------------------------
# OAuth：微信
# ---------------------------------------------------------------------------


@app.get("/auth/wechat")
async def wechat_login(request: Request, redirect: str = Query("")):
    """跳转到微信 OAuth 授权页"""
    wx_config = CONFIG.get("wechat", {})
    app_id = wx_config.get("app_id", "")
    if not app_id:
        raise HTTPException(500, "WeChat OAuth not configured")

    state = f"{redirect}|{secrets.token_hex(8)}"
    auth_url = (
        "https://open.weixin.qq.com/connect/qrconnect?"
        f"appid={app_id}"
        f"&redirect_uri={BASE_URL}/auth/wechat/callback"
        f"&response_type=code"
        f"&scope=snsapi_login"
        f"&state={state}"
        "#wechat_redirect"
    )
    return RedirectResponse(auth_url)


@app.get("/auth/wechat/callback")
async def wechat_callback(
    request: Request,
    code: str = Query(""),
    state: str = Query(""),
):
    """微信 OAuth 回调"""
    wx_config = CONFIG.get("wechat", {})
    app_id = wx_config.get("app_id", "")
    app_secret = wx_config.get("app_secret", "")

    if not code:
        raise HTTPException(400, "Missing code")

    async with httpx.AsyncClient() as client:
        # 用 code 换取 access_token + openid
        token_resp = await client.get(
            "https://api.weixin.qq.com/sns/oauth2/access_token",
            params={
                "appid": app_id,
                "secret": app_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token", "")
        openid = token_data.get("openid", "")

        if not openid:
            raise HTTPException(502, "Failed to get WeChat token")

        # 获取用户信息
        user_resp = await client.get(
            "https://api.weixin.qq.com/sns/userinfo",
            params={"access_token": access_token, "openid": openid},
        )
        user_data = user_resp.json()

    visitor_id = f"wechat_{openid}"
    name = user_data.get("nickname", "微信用户")
    avatar = user_data.get("headimgurl", "")

    redirect_target = ""
    if state and "|" in state:
        redirect_target = state.split("|", 1)[0]

    return await _register_visitor_and_redirect(
        request, visitor_id, "wechat", name, avatar, redirect_target
    )


# ---------------------------------------------------------------------------
# 访客注册 + 重定向（OAuth 回调共用）
# ---------------------------------------------------------------------------


async def _register_visitor_and_redirect(
    request: Request,
    visitor_id: str,
    provider: str,
    name: str,
    avatar: str,
    redirect_target: str,
) -> Response:
    visitors_data = read_visitors()
    visitor = None
    for v in visitors_data["visitors"]:
        if v["id"] == visitor_id:
            visitor = v
            v["name"] = name
            v["avatar"] = avatar
            break

    if not visitor:
        visitor = {
            "id": visitor_id,
            "provider": provider,
            "name": name,
            "avatar": avatar,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "approved_pages": [],
            "pending_pages": [],
            "whitelisted_owners": [],
            "blocked": False,
        }
        visitors_data["visitors"].append(visitor)

    # 如果有 redirect_target，自动提交访问申请
    should_notify = False
    notify_page = None
    if redirect_target:
        notify_page = _find_page_for_redirect_target(redirect_target)
        if notify_page and notify_page.get("access") == "approval":
            owner_scope = _page_owner(notify_page)
            if (
                not _visitor_has_owner_whitelist(visitor, owner_scope)
                and notify_page["slug"] not in visitor.get("approved_pages", [])
                and notify_page["slug"] not in visitor.get("pending_pages", [])
            ):
                visitor.setdefault("pending_pages", []).append(notify_page["slug"])
                should_notify = True

    write_visitors(visitors_data)

    # 推送 OpenClaw 通知
    if should_notify and notify_page:
        await notify_openclaw(notify_page, visitor)

    # 设置 session cookie 并重定向
    redirect_url = f"/{redirect_target.lstrip('/')}" if redirect_target else "/"
    response = RedirectResponse(redirect_url, status_code=302)
    set_session_cookie(response, visitor_id)
    return response


# ---------------------------------------------------------------------------
# API：轮询审批状态
# ---------------------------------------------------------------------------


@app.get("/api/check-approval/{slug}")
async def check_approval(slug: str, request: Request):
    visitor = get_session_visitor(request)
    if not visitor:
        return {"status": "not_logged_in"}
    page = find_page(slug)
    if page and _visitor_has_owner_whitelist(visitor, _page_owner(page)):
        return {"status": "approved"}
    if slug in visitor.get("approved_pages", []):
        return {"status": "approved"}
    if slug in visitor.get("pending_pages", []):
        return {"status": "pending"}
    return {"status": "not_requested"}


@app.get("/api/check-approval/stream/{slug}")
async def check_approval_stream(slug: str, request: Request):
    visitor = get_session_visitor(request)
    if not visitor:
        async def not_logged_in():
            yield _format_sse_event(retry_ms=SSE_RETRY_MS, event="status", data={"status": "not_logged_in"})
        return StreamingResponse(not_logged_in(), media_type="text/event-stream")

    visitor_id = visitor["id"]
    key = _approval_key(slug, visitor_id)

    async def generate():
        queue = asyncio.Queue(maxsize=SSE_APPROVAL_QUEUE_SIZE)
        APPROVAL_SUBSCRIBERS.setdefault(key, set()).add(queue)
        try:
            yield _format_sse_event(retry_ms=SSE_RETRY_MS, comment="connected")
            page = find_page(slug)
            if page and _visitor_has_owner_whitelist(visitor, _page_owner(page)):
                yield _format_sse_event(event="status", data={"status": "approved"})
                return
            if slug in visitor.get("approved_pages", []):
                yield _format_sse_event(event="status", data={"status": "approved"})
                return
            if slug not in visitor.get("pending_pages", []):
                yield _format_sse_event(event="status", data={"status": "not_requested"})
                return
            yield _format_sse_event(event="status", data={"status": "pending"})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_INTERVAL_SEC)
                    yield _format_sse_event(event="status", data=event)
                    if event.get("status") in ("approved", "rejected"):
                        break
                except asyncio.TimeoutError:
                    yield _format_sse_event(comment="ping")
        finally:
            if key in APPROVAL_SUBSCRIBERS:
                APPROVAL_SUBSCRIBERS[key].discard(queue)
                if not APPROVAL_SUBSCRIBERS[key]:
                    APPROVAL_SUBSCRIBERS.pop(key, None)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/pending")
async def get_all_pending(user=Depends(verify_admin)):
    """获取当前用户拥有的页面的待审批请求"""
    visitors_data = read_visitors()
    index = read_index()
    my_pages = _get_user_pages(user, index)
    my_slugs = {p["slug"] for p in my_pages}
    title_map = {p["slug"]: p["title"] for p in my_pages}
    provider_names = {"dingtalk": "钉钉", "wechat": "微信"}

    pending_list = []
    for v in visitors_data["visitors"]:
        for slug in v.get("pending_pages", []):
            if slug in my_slugs:
                pending_list.append({
                    "slug": slug,
                    "page_title": title_map.get(slug, slug),
                    "visitor_id": v["id"],
                    "visitor_name": v["name"],
                    "provider": provider_names.get(v.get("provider", ""), v.get("provider", "")),
                })

    return {"pending": pending_list, "count": len(pending_list)}


@app.get("/api/events/stream")
async def event_stream(request: Request, user=Depends(verify_admin)):
    last_event_id = request.headers.get("Last-Event-ID", "") or request.query_params.get("last_event_id", "")

    # 预取当前用户拥有的页面 slug 集合
    index = read_index()
    my_slugs = {p["slug"] for p in _get_user_pages(user, index)}
    is_super = _is_super_admin(user)

    def _event_belongs_to_user(event: dict) -> bool:
        if is_super:
            return True
        slug = event.get("page", {}).get("slug", "")
        return slug in my_slugs

    async def generate():
        queue = asyncio.Queue(maxsize=SSE_EVENT_QUEUE_SIZE)
        subscriber = {
            "is_super": is_super,
            "slugs": frozenset(my_slugs),
            "disconnect": False,
        }
        EVENT_SUBSCRIBERS[queue] = subscriber
        try:
            yield _format_sse_event(retry_ms=SSE_RETRY_MS, comment="connected")
            if last_event_id:
                replay = False
                for item in EVENT_HISTORY:
                    if replay and _event_belongs_to_user(item):
                        yield _format_sse_event(event_id=item["id"], event=item["type"], data=item)
                    elif item["id"] == last_event_id:
                        replay = True
            while True:
                if await request.is_disconnected():
                    break
                if subscriber.get("disconnect"):
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_INTERVAL_SEC)
                    yield _format_sse_event(event_id=event["id"], event=event["type"], data=event)
                except asyncio.TimeoutError:
                    yield _format_sse_event(comment="ping")
        finally:
            EVENT_SUBSCRIBERS.pop(queue, None)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


# ---------------------------------------------------------------------------
# 路由：页面访问（鉴权入口）— 放在最后避免匹配其他路由
# ---------------------------------------------------------------------------


def _page_asset_response(page: dict, path: str) -> Response:
    file_path = PAGES_DIR / page["slug"] / "assets" / path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404)
    try:
        file_path.resolve().relative_to((PAGES_DIR / page["slug"] / "assets").resolve())
    except ValueError:
        raise HTTPException(403)

    import mimetypes
    content_type, _ = mimetypes.guess_type(str(file_path))
    return Response(
        content=file_path.read_bytes(),
        media_type=content_type or "application/octet-stream",
    )


@app.get("/{username}/{slug}/assets/{path:path}")
async def owner_page_assets(username: str, slug: str, path: str):
    page = _find_page_for_username_slug(username, slug)
    if not page:
        raise HTTPException(404)
    return _page_asset_response(page, path)


@app.get("/{username}/{slug}", response_class=HTMLResponse)
async def view_owner_page_redirect(username: str, slug: str, request: Request):
    page = _find_page_for_username_slug(username, slug)
    if not page:
        raise HTTPException(404, "Page not found")
    return RedirectResponse(_page_path(page), status_code=307)


@app.get("/{username}/{slug}/", response_class=HTMLResponse)
async def view_owner_page(username: str, slug: str, request: Request):
    page = _find_page_for_username_slug(username, slug)
    if not page:
        raise HTTPException(404, "Page not found")
    return await _render_page_access(page, request, redirect_target=_page_redirect_target(page))


@app.get("/{short}", response_class=HTMLResponse)
async def view_short_path(short: str, request: Request):
    if short in ("favicon.ico", "robots.txt"):
        raise HTTPException(404)

    owner = find_user_by_username(short)
    if owner:
        return templates.TemplateResponse(
            "owner_home.html",
            template_context(
                request=request,
                **_owner_home_context(owner, request),
            ),
        )

    page = find_page(short)
    if not page:
        raise HTTPException(404, "Page not found")

    canonical_path = _page_path(page)
    if canonical_path != f"/{short}":
        return RedirectResponse(canonical_path, status_code=307)

    return await _render_page_access(page, request, redirect_target=_page_redirect_target(page))


# 静态资源服务
@app.get("/{slug}/assets/{path:path}")
async def page_assets(slug: str, path: str):
    page = find_page(slug)
    if not page:
        raise HTTPException(404)
    canonical_path = _page_path(page)
    if canonical_path != f"/{slug}":
        return RedirectResponse(f"{canonical_path}assets/{path}", status_code=307)
    return _page_asset_response(page, path)


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    host = SERVER_CONFIG.get("host", "0.0.0.0")
    port = SERVER_CONFIG.get("port", 8000)
    print(f"🚀 PageGate running at http://{host}:{port}")
    uvicorn.run(
        app,
        host=host,
        port=port,
        timeout_keep_alive=max(5, int(SERVER_CONFIG.get("keep_alive_timeout_sec", 15))),
        timeout_graceful_shutdown=max(3, int(SERVER_CONFIG.get("graceful_shutdown_timeout_sec", 10))),
    )
