from dotenv import load_dotenv
from pathlib import Path
import os

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import logging
import re
import uuid
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field
from typing import List, Optional

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI()
api_router = APIRouter(prefix="/api")

JWT_ALGORITHM = "HS256"
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# ---------------- Auth helpers ----------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=JWT_ALGORITHM)


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALGORITHM])
        user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ---------------- Models ----------------
def now_iso():
    return datetime.now(timezone.utc).isoformat()


class LoginInput(BaseModel):
    email: str
    password: str


class ScienceIn(BaseModel):
    name: str
    arabic_name: Optional[str] = ""
    description: Optional[str] = ""


class BookIn(BaseModel):
    science_id: str
    title: str
    arabic_title: Optional[str] = ""
    author: Optional[str] = ""
    difficulty: str = "primer"  # primer | intermediate | advanced
    tag: Optional[str] = ""  # madhhab / era


class TopicIn(BaseModel):
    science_id: str
    parent_id: Optional[str] = None
    title: str
    arabic_title: Optional[str] = ""
    intro_html: Optional[str] = ""


class TopicUpdate(BaseModel):
    title: Optional[str] = None
    arabic_title: Optional[str] = None
    intro_html: Optional[str] = None


class MoveIn(BaseModel):
    parent_id: Optional[str] = None
    before_id: Optional[str] = None


class NoteIn(BaseModel):
    topic_id: str
    book_id: str
    arabic_text: Optional[str] = ""
    english_html: Optional[str] = ""


class NoteUpdate(BaseModel):
    arabic_text: Optional[str] = None
    english_html: Optional[str] = None


class MiscNoteIn(BaseModel):
    topic_id: str
    title: Optional[str] = ""
    content_html: str


class MiscNoteUpdate(BaseModel):
    title: Optional[str] = None
    content_html: Optional[str] = None


class ReorderIn(BaseModel):
    ids: List[str]


def strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "").strip()


# ---------------- Auth routes ----------------
@api_router.post("/auth/login")
async def login(data: LoginInput):
    email = data.email.strip().lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user["id"], user["email"])
    return {
        "access_token": token,
        "user": {"id": user["id"], "email": user["email"], "name": user.get("name", "Admin"), "role": user.get("role", "admin")},
    }


@api_router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


# ---------------- Sciences ----------------
@api_router.get("/sciences")
async def list_sciences():
    items = await db.sciences.find({}, {"_id": 0}).sort("order", 1).to_list(1000)
    return items


@api_router.post("/sciences")
async def create_science(data: ScienceIn, user: dict = Depends(get_current_user)):
    count = await db.sciences.count_documents({})
    doc = {"id": str(uuid.uuid4()), **data.model_dump(), "order": count, "created_at": now_iso()}
    await db.sciences.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api_router.put("/sciences/{sid}")
async def update_science(sid: str, data: ScienceIn, user: dict = Depends(get_current_user)):
    await db.sciences.update_one({"id": sid}, {"$set": data.model_dump()})
    return await db.sciences.find_one({"id": sid}, {"_id": 0})


@api_router.delete("/sciences/{sid}")
async def delete_science(sid: str, user: dict = Depends(get_current_user)):
    topic_ids = [t["id"] for t in await db.topics.find({"science_id": sid}, {"id": 1}).to_list(10000)]
    await db.notes.delete_many({"topic_id": {"$in": topic_ids}})
    await db.miscnotes.delete_many({"topic_id": {"$in": topic_ids}})
    await db.topics.delete_many({"science_id": sid})
    await db.books.delete_many({"science_id": sid})
    await db.sciences.delete_one({"id": sid})
    return {"ok": True}


@api_router.post("/sciences/reorder")
async def reorder_sciences(data: ReorderIn, user: dict = Depends(get_current_user)):
    for i, sid in enumerate(data.ids):
        await db.sciences.update_one({"id": sid}, {"$set": {"order": i}})
    return {"ok": True}


# ---------------- Books ----------------
@api_router.get("/sciences/{sid}/books")
async def list_books(sid: str):
    items = await db.books.find({"science_id": sid}, {"_id": 0}).sort("order", 1).to_list(1000)
    return items


@api_router.post("/books")
async def create_book(data: BookIn, user: dict = Depends(get_current_user)):
    count = await db.books.count_documents({"science_id": data.science_id})
    doc = {"id": str(uuid.uuid4()), **data.model_dump(), "order": count, "created_at": now_iso()}
    await db.books.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api_router.put("/books/{bid}")
async def update_book(bid: str, data: BookIn, user: dict = Depends(get_current_user)):
    await db.books.update_one({"id": bid}, {"$set": data.model_dump()})
    return await db.books.find_one({"id": bid}, {"_id": 0})


@api_router.delete("/books/{bid}")
async def delete_book(bid: str, user: dict = Depends(get_current_user)):
    await db.notes.delete_many({"book_id": bid})
    await db.books.delete_one({"id": bid})
    return {"ok": True}


@api_router.post("/books/reorder")
async def reorder_books(data: ReorderIn, user: dict = Depends(get_current_user)):
    for i, bid in enumerate(data.ids):
        await db.books.update_one({"id": bid}, {"$set": {"order": i}})
    return {"ok": True}


# ---------------- Topics (nested tree) ----------------
@api_router.get("/sciences/{sid}/topics")
async def list_topics(sid: str):
    items = await db.topics.find({"science_id": sid}, {"_id": 0}).sort("order", 1).to_list(10000)
    return items


@api_router.get("/topics/{tid}")
async def get_topic(tid: str):
    topic = await db.topics.find_one({"id": tid}, {"_id": 0})
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    # breadcrumb
    trail = []
    cur = topic
    while cur:
        trail.insert(0, {"id": cur["id"], "title": cur["title"]})
        if cur.get("parent_id"):
            cur = await db.topics.find_one({"id": cur["parent_id"]}, {"_id": 0})
        else:
            cur = None
    topic["breadcrumb"] = trail
    return topic


@api_router.post("/topics")
async def create_topic(data: TopicIn, user: dict = Depends(get_current_user)):
    count = await db.topics.count_documents({"science_id": data.science_id, "parent_id": data.parent_id})
    doc = {"id": str(uuid.uuid4()), **data.model_dump(), "order": count, "created_at": now_iso()}
    await db.topics.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api_router.put("/topics/{tid}")
async def update_topic(tid: str, data: TopicUpdate, user: dict = Depends(get_current_user)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    if update:
        await db.topics.update_one({"id": tid}, {"$set": update})
    return await db.topics.find_one({"id": tid}, {"_id": 0})


async def _collect_descendants(tid: str) -> List[str]:
    result = [tid]
    children = await db.topics.find({"parent_id": tid}, {"id": 1}).to_list(10000)
    for c in children:
        result.extend(await _collect_descendants(c["id"]))
    return result


@api_router.delete("/topics/{tid}")
async def delete_topic(tid: str, user: dict = Depends(get_current_user)):
    ids = await _collect_descendants(tid)
    await db.notes.delete_many({"topic_id": {"$in": ids}})
    await db.miscnotes.delete_many({"topic_id": {"$in": ids}})
    await db.topics.delete_many({"id": {"$in": ids}})
    return {"ok": True}


@api_router.post("/topics/reorder")
async def reorder_topics(data: ReorderIn, user: dict = Depends(get_current_user)):
    for i, tid in enumerate(data.ids):
        await db.topics.update_one({"id": tid}, {"$set": {"order": i}})
    return {"ok": True}


@api_router.put("/topics/{tid}/move")
async def move_topic(tid: str, data: MoveIn, user: dict = Depends(get_current_user)):
    topic = await db.topics.find_one({"id": tid}, {"_id": 0})
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    new_parent = data.parent_id or None
    if new_parent == tid:
        raise HTTPException(status_code=400, detail="Cannot move a topic into itself")
    if new_parent:
        if new_parent in await _collect_descendants(tid):
            raise HTTPException(status_code=400, detail="Cannot move a topic into its own subtopic")
        parent = await db.topics.find_one({"id": new_parent}, {"_id": 0})
        if not parent:
            raise HTTPException(status_code=404, detail="Target parent not found")
    await db.topics.update_one({"id": tid}, {"$set": {"parent_id": new_parent}})
    siblings = await db.topics.find(
        {"science_id": topic["science_id"], "parent_id": new_parent}, {"_id": 0, "id": 1}
    ).sort("order", 1).to_list(10000)
    ids = [s["id"] for s in siblings if s["id"] != tid]
    if data.before_id and data.before_id in ids:
        ids.insert(ids.index(data.before_id), tid)
    else:
        ids.append(tid)
    for i, sid in enumerate(ids):
        await db.topics.update_one({"id": sid}, {"$set": {"order": i}})
    return {"ok": True}


# ---------------- Notes (per book, per topic) ----------------
@api_router.get("/topics/{tid}/notes")
async def list_notes(tid: str):
    notes = await db.notes.find({"topic_id": tid}, {"_id": 0}).sort("order", 1).to_list(1000)
    return notes


@api_router.post("/notes")
async def create_note(data: NoteIn, user: dict = Depends(get_current_user)):
    count = await db.notes.count_documents({"topic_id": data.topic_id})
    doc = {"id": str(uuid.uuid4()), **data.model_dump(), "order": count, "created_at": now_iso(), "updated_at": now_iso()}
    await db.notes.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api_router.put("/notes/{nid}")
async def update_note(nid: str, data: NoteUpdate, user: dict = Depends(get_current_user)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    update["updated_at"] = now_iso()
    await db.notes.update_one({"id": nid}, {"$set": update})
    return await db.notes.find_one({"id": nid}, {"_id": 0})


@api_router.delete("/notes/{nid}")
async def delete_note(nid: str, user: dict = Depends(get_current_user)):
    await db.notes.delete_one({"id": nid})
    return {"ok": True}


@api_router.post("/notes/reorder")
async def reorder_notes(data: ReorderIn, user: dict = Depends(get_current_user)):
    for i, nid in enumerate(data.ids):
        await db.notes.update_one({"id": nid}, {"$set": {"order": i}})
    return {"ok": True}


# ---------------- Misc notes ----------------
@api_router.get("/topics/{tid}/miscnotes")
async def list_miscnotes(tid: str):
    items = await db.miscnotes.find({"topic_id": tid}, {"_id": 0}).sort("order", 1).to_list(1000)
    return items


@api_router.post("/miscnotes")
async def create_miscnote(data: MiscNoteIn, user: dict = Depends(get_current_user)):
    count = await db.miscnotes.count_documents({"topic_id": data.topic_id})
    doc = {"id": str(uuid.uuid4()), **data.model_dump(), "order": count, "created_at": now_iso(), "updated_at": now_iso()}
    await db.miscnotes.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api_router.put("/miscnotes/{mid}")
async def update_miscnote(mid: str, data: MiscNoteUpdate, user: dict = Depends(get_current_user)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    update["updated_at"] = now_iso()
    await db.miscnotes.update_one({"id": mid}, {"$set": update})
    return await db.miscnotes.find_one({"id": mid}, {"_id": 0})


@api_router.delete("/miscnotes/{mid}")
async def delete_miscnote(mid: str, user: dict = Depends(get_current_user)):
    await db.miscnotes.delete_one({"id": mid})
    return {"ok": True}


# ---------------- Search ----------------
@api_router.get("/search")
async def search(q: str, science_id: Optional[str] = None):
    q = (q or "").strip()
    if not q:
        return []
    rx = re.compile(re.escape(q), re.IGNORECASE)
    results = []
    seen = set()

    topic_filter = {"$or": [{"title": rx}, {"arabic_title": rx}]}
    if science_id:
        topic_filter = {"$and": [{"science_id": science_id}, topic_filter]}
    topics = await db.topics.find(topic_filter, {"_id": 0}).to_list(200)
    for t in topics:
        key = ("topic", t["id"])
        if key in seen:
            continue
        seen.add(key)
        results.append({"type": "topic", "topic_id": t["id"], "science_id": t["science_id"], "title": t["title"], "snippet": t.get("arabic_title", "")})

    note_filter = {"$or": [{"english_html": rx}, {"arabic_text": rx}]}
    notes = await db.notes.find(note_filter, {"_id": 0}).to_list(400)
    for n in notes:
        plain = strip_html(n.get("english_html", ""))
        # avoid false positives on HTML tag names
        if not (rx.search(plain) or rx.search(n.get("arabic_text", ""))):
            continue
        topic = await db.topics.find_one({"id": n["topic_id"]}, {"_id": 0})
        if not topic:
            continue
        if science_id and topic["science_id"] != science_id:
            continue
        book = await db.books.find_one({"id": n["book_id"]}, {"_id": 0})
        text = strip_html(n.get("english_html", "")) or n.get("arabic_text", "")
        m = rx.search(text)
        snippet = text
        if m:
            start = max(0, m.start() - 40)
            snippet = ("…" if start > 0 else "") + text[start:start + 160] + "…"
        key = ("note", n["id"])
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "type": "note",
            "topic_id": n["topic_id"],
            "science_id": topic["science_id"],
            "title": topic["title"],
            "book_title": book["title"] if book else "",
            "snippet": snippet,
        })
    return results[:50]


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------- Seed ----------------
async def seed_admin():
    email = os.environ["ADMIN_EMAIL"].strip().lower()
    password = os.environ["ADMIN_PASSWORD"]
    existing = await db.users.find_one({"email": email})
    if not existing:
        await db.users.insert_one({
            "id": str(uuid.uuid4()), "email": email, "password_hash": hash_password(password),
            "name": "Admin", "role": "admin", "created_at": now_iso(),
        })
    elif not verify_password(password, existing["password_hash"]):
        await db.users.update_one({"email": email}, {"$set": {"password_hash": hash_password(password)}})


async def seed_content():
    if await db.sciences.count_documents({}) > 0:
        return
    sci = {"id": str(uuid.uuid4()), "name": "Usul al-Fiqh", "arabic_name": "أصول الفقه",
           "description": "The principles of Islamic jurisprudence.", "order": 0, "created_at": now_iso()}
    await db.sciences.insert_one(sci)
    sid = sci["id"]

    books = [
        {"title": "Matn al-Waraqat", "arabic_title": "متن الورقات", "author": "Imam al-Juwayni", "difficulty": "primer", "tag": "Shafi'i · 478 AH"},
        {"title": "Lubb al-Usul", "arabic_title": "لب الأصول", "author": "Zakariyya al-Ansari", "difficulty": "intermediate", "tag": "Shafi'i · 926 AH"},
        {"title": "Al-Mahsul", "arabic_title": "المحصول", "author": "Fakhr al-Din al-Razi", "difficulty": "advanced", "tag": "Shafi'i · 606 AH"},
    ]
    book_ids = []
    for i, b in enumerate(books):
        bid = str(uuid.uuid4())
        book_ids.append(bid)
        await db.books.insert_one({"id": bid, "science_id": sid, "order": i, "created_at": now_iso(), **b})

    # Top topic: Al-Adillah (the evidences) with children
    parent = {"id": str(uuid.uuid4()), "science_id": sid, "parent_id": None, "title": "Al-Adillah (The Evidences)",
              "arabic_title": "الأدلة", "order": 0, "created_at": now_iso()}
    await db.topics.insert_one(parent)

    children = [
        ("Al-Khaas (The Specific)", "الخاص"),
        ("Al-Aam (The General)", "العام"),
        ("Al-Amr (The Command)", "الأمر"),
        ("Al-Nahy (The Prohibition)", "النهي"),
    ]
    for i, (title, ar) in enumerate(children):
        tid = str(uuid.uuid4())
        await db.topics.insert_one({"id": tid, "science_id": sid, "parent_id": parent["id"], "title": title,
                                    "arabic_title": ar, "order": i, "created_at": now_iso()})
        if i == 0:  # Al-Khaas gets sample notes
            samples = [
                ("العام هو اللفظ المستغرق لجميع ما يصلح له",
                 "<p><strong>Al-Khaas</strong> is a term that indicates a <em>single, specific</em> meaning and does not extend to encompass many individuals. It is the opposite of al-'Aam (the general).</p><ul><li>A specific word designates one determinate meaning.</li><li>It acts decisively upon what it denotes.</li></ul>"),
                ("الخاص قسيم العام",
                 "<p>The author elaborates that al-Khaas is the counterpart of al-'Aam. Whereas the general word admits <em>takhsis</em> (specification), the specific word carries a definitive ruling upon its subject.</p>"),
                ("",
                 "<p>Al-Razi offers a detailed epistemological treatment, distinguishing the linguistic from the legal usage of specification, and addressing the debates on whether the specific implies certainty.</p><blockquote>The specific yields decisive knowledge upon its referent unless a contextual indicator suggests otherwise.</blockquote>"),
            ]
            for j, (ar_txt, en) in enumerate(samples):
                await db.notes.insert_one({"id": str(uuid.uuid4()), "topic_id": tid, "book_id": book_ids[j],
                                           "arabic_text": ar_txt, "english_html": en, "order": j,
                                           "created_at": now_iso(), "updated_at": now_iso()})
            await db.miscnotes.insert_one({"id": str(uuid.uuid4()), "topic_id": tid, "title": "Study reminder",
                                           "content_html": "<p>Compare how the primer treats al-Khaas versus the depth in al-Mahsul. Note the shift from definition to epistemology.</p>",
                                           "order": 0, "created_at": now_iso(), "updated_at": now_iso()})


@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await seed_admin()
    await seed_content()


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
