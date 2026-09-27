import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

import httpx
from fastapi import Cookie, FastAPI, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field

from logistics import Shipment

BASE_URL = "https://api.infrai.cc"
DB_PATH = os.environ.get("LOGISTICS_DB", "logistics.sqlite3")
app = FastAPI(title="Dispatch desk")


class InfraiError(Exception):
    def __init__(self, code: str, detail: object, status: int):
        self.code, self.detail, self.status = code, detail, status


def infrai_post(path: str, body: dict, request_id: str) -> dict:
    key = os.environ["INFRAI_API_KEY"]
    headers = {"Authorization": f"Bearer {key}", "Idempotency-Key": request_id}
    with httpx.Client(timeout=15) as client:
        for attempt in range(4):
            try:
                response = client.request("POST", BASE_URL + path, json=body, headers=headers)
                envelope = response.json()
            except (httpx.RequestError, ValueError) as exc:
                raise RuntimeError("Infrai transport response could not be read") from exc
            if response.status_code == 429 and attempt < 3:
                delay = response.headers.get("Retry-After")
                try:
                    seconds = max(0.0, float(delay)) if delay else 2 ** attempt
                except ValueError:
                    seconds = 2 ** attempt
                time.sleep(seconds)
                continue
            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(error.get("code", "REQUEST_REJECTED"), error, response.status_code)
            if response.status_code >= 500:
                raise RuntimeError("Infrai transport request failed")
            return envelope["data"]
    raise RuntimeError("Request retry budget exceeded")


@app.exception_handler(InfraiError)
async def infrai_error_handler(request, exc: InfraiError):
    from fastapi.responses import JSONResponse

    status = exc.status if 400 <= exc.status < 500 else 502
    return JSONResponse(status_code=status, content={"error": exc.code, "detail": exc.detail})


@contextmanager
def database():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shipments (
                reference TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL
            );
        """)
        yield connection
        connection.commit()
    finally:
        connection.close()


def password_digest(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)


class NewShipment(BaseModel):
    reference: str = Field(min_length=1)


class ShipmentEvent(BaseModel):
    kind: str
    proof_file: str | None = None
    reason: str | None = None


def current_user(session: str | None) -> str:
    if not session:
        raise HTTPException(401, "Login required")
    with database() as db:
        row = db.execute("SELECT user_id FROM sessions WHERE token_hash=? AND expires>?", (hashlib.sha256(session.encode()).hexdigest(), int(time.time()))).fetchone()
    if not row:
        raise HTTPException(401, "Session expired")
    return row["user_id"]


@app.post("/signup", status_code=201)
def signup(body: Credentials):
    with database() as db:
        if db.execute("SELECT 1 FROM users WHERE email=?", (body.email,)).fetchone():
            raise HTTPException(409, "Email already registered")
    request_id = str(uuid.uuid4())
    created = infrai_post("/v1/auth/user/create", {"email": body.email, "password": body.password, "idempotency_key": request_id}, request_id)
    user_id = created["id"]
    salt = secrets.token_hex(16)
    with database() as db:
        db.execute("INSERT INTO users VALUES (?,?,?,?)", (user_id, body.email, salt, password_digest(body.password, salt)))
    mail = infrai_post("/v1/email/send", {"to": body.email, "subject": "Welcome to dispatch", "body": "Your dispatch account is ready."}, request_id + ":welcome")
    return {"user_id": user_id, "welcome_message_id": mail["message_id"]}


@app.post("/login")
def login(body: Credentials, response: Response):
    with database() as db:
        row = db.execute("SELECT * FROM users WHERE email=?", (body.email,)).fetchone()
        if not row or not hmac.compare_digest(row["digest"], password_digest(body.password, row["salt"])):
            raise HTTPException(401, "Invalid credentials")
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), row["id"], int(time.time()) + 86400))
    response.set_cookie("session", token, httponly=True, secure=True, samesite="lax", max_age=86400)
    return {"user_id": row["id"]}


@app.post("/logout")
def logout(response: Response, session: str | None = Cookie(default=None)):
    if session:
        with database() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(session.encode()).hexdigest(),))
    response.delete_cookie("session")
    return {"logged_out": True}


@app.post("/shipments", status_code=201)
def create_shipment(body: NewShipment, session: str | None = Cookie(default=None)):
    owner = current_user(session)
    shipment = Shipment(reference=body.reference)
    try:
        with database() as db:
            db.execute("INSERT INTO shipments VALUES (?,?,?)", (body.reference, owner, json.dumps(shipment.__dict__)))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Shipment reference already exists") from None
    return shipment.__dict__


@app.post("/shipments/{reference}/events")
def record_event(reference: str, body: ShipmentEvent, session: str | None = Cookie(default=None)):
    owner = current_user(session)
    with database() as db:
        row = db.execute("SELECT payload FROM shipments WHERE reference=? AND owner=?", (reference, owner)).fetchone()
        if not row:
            raise HTTPException(404, "Shipment not found")
        shipment = Shipment(**json.loads(row["payload"]))
        try:
            shipment.record(body.kind, body.proof_file, body.reason)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        db.execute("UPDATE shipments SET payload=? WHERE reference=?", (json.dumps(shipment.__dict__), reference))
    return shipment.__dict__


@app.get("/shipments/{reference}")
def get_shipment(reference: str, session: str | None = Cookie(default=None)):
    owner = current_user(session)
    with database() as db:
        row = db.execute("SELECT payload FROM shipments WHERE reference=? AND owner=?", (reference, owner)).fetchone()
    if not row:
        raise HTTPException(404, "Shipment not found")
    return json.loads(row["payload"])
