from dotenv import load_dotenv
from pathlib import Path
import os

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request
from starlette.middleware.cors import CORSMiddleware
import logging
import re
import uuid
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
from pydantic import BaseModel
from typing import List, Optional

from sqlalchemy import String, Integer, select, func, delete as sql_delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.mysql import LONGTEXT

# ---------------- Database ----------------
DB_USER = os.environ['DB_USER']
DB_PASSWORD = os.environ['DB_PASSWORD']
DB_HOST = os.environ['DB_HOST']
DB_PORT = os.environ.get('DB_PORT', '3306')
DB_NAME = os.environ['DB_NAME']

DATABASE_URL = (
    f"mysql+aiomysql://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
)

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

app = FastAPI()
api_router = APIRouter(prefix="/api")

JWT_ALGORITHM = "HS256"
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def uid() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------- ORM models ----------------
class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(191), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), default="Admin")
    role: Mapped[str] = mapped_column(String(50), default="admin")
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Science(Base):
    __tablename__ = "sciences"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(255))
    arabic_name: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[Optional[str]] = mapped_column(LONGTEXT, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Book(Base):
    __tablename__ = "books"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    science_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(500))
    arabic_title: Mapped[str] = mapped_column(String(500), default="")
    author: Mapped[str] = mapped_column(String(255), default="")
    difficulty: Mapped[str] = mapped_column(String(20), default="primer")
    tag: Mapped[str] = mapped_column(String(255), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Topic(Base):
    __tablename__ = "topics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    science_id: Mapped[str] = mapped_column(String(36), index=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    arabic_title: Mapped[str] = mapped_column(String(500), default="")
    intro_html: Mapped[Optional[str]] = mapped_column(LONGTEXT, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    topic_id: Mapped[str] = mapped_column(String(36), index=True)
    book_id: Mapped[str] = mapped_column(String(36), index=True)
    arabic_text: Mapped[Optional[str]] = mapped_column(LONGTEXT, nullable=True)
    english_html: Mapped[Optional[str]] = mapped_column(LONGTEXT, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso)


class MiscNote(Base):
    __tablename__ = "misc_notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    topic_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    content_html: Mapped[Optional[str]] = mapped_column(LONGTEXT, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=now_iso)


# ---------------- Serializers ----------------
def sci_dict(s: Science) -> dict:
    return {"id": s.id, "name": s.name, "arabic_name": s.arabic_name or "", "description": s.description or "", "order": s.sort_order, "created_at": s.created_at}


def book_dict(b: Book) -> dict:
    return {"id": b.id, "science_id": b.science_id, "title": b.title, "arabic_title": b.arabic_title or "", "author": b.author or "", "difficulty": b.difficulty, "tag": b.tag or "", "order": b.sort_order, "created_at": b.created_at}


def topic_dict(t: Topic) -> dict:
    return {"id": t.id, "science_id": t.science_id, "parent_id": t.parent_id, "title": t.title, "arabic_title": t.arabic_title or "", "intro_html": t.intro_html or "", "order": t.sort_order, "created_at": t.created_at}


def note_dict(n: Note) -> dict:
    return {"id": n.id, "topic_id": n.topic_id, "book_id": n.book_id, "arabic_text": n.arabic_text or "", "english_html": n.english_html or "", "order": n.sort_order, "created_at": n.created_at, "updated_at": n.updated_at}


def misc_dict(m: MiscNote) -> dict:
    return {"id": m.id, "topic_id": m.topic_id, "title": m.title or "", "content_html": m.content_html or "", "order": m.sort_order, "created_at": m.created_at, "updated_at": m.updated_at}


# ---------------- Auth helpers ----------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: str, email: str) -> str:
    payload = {"sub": user_id, "email": email, "type": "access", "exp": datetime.now(timezone.utc) + timedelta(days=7)}
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=JWT_ALGORITHM)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


# ---------------- Pydantic I/O ----------------
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
    difficulty: str = "primer"
    tag: Optional[str] = ""


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


async def _count(db: AsyncSession, model, **filters) -> int:
    stmt = select(func.count()).select_from(model)
    for k, v in filters.items():
        stmt = stmt.where(getattr(model, k) == v)
    return (await db.execute(stmt)).scalar() or 0


async def collect_descendants(db: AsyncSession, tid: str) -> List[str]:
    result = [tid]
    child_ids = (await db.execute(select(Topic.id).where(Topic.parent_id == tid))).scalars().all()
    for cid in child_ids:
        result.extend(await collect_descendants(db, cid))
    return result


# ---------------- Auth routes ----------------
@api_router.post("/auth/login")
async def login(data: LoginInput, db: AsyncSession = Depends(get_db)):
    email = data.email.strip().lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user.id, user.email)
    return {"access_token": token, "user": {"id": user.id, "email": user.email, "name": user.name, "role": user.role}}


@api_router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


# ---------------- Sciences ----------------
@api_router.get("/sciences")
async def list_sciences(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Science).order_by(Science.sort_order))).scalars().all()
    return [sci_dict(s) for s in rows]


@api_router.post("/sciences")
async def create_science(data: ScienceIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    s = Science(id=uid(), name=data.name, arabic_name=data.arabic_name or "", description=data.description or "", sort_order=await _count(db, Science), created_at=now_iso())
    db.add(s)
    await db.commit()
    return sci_dict(s)


@api_router.put("/sciences/{sid}")
async def update_science(sid: str, data: ScienceIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(Science).where(Science.id == sid))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Science not found")
    s.name = data.name
    s.arabic_name = data.arabic_name or ""
    s.description = data.description or ""
    await db.commit()
    return sci_dict(s)


@api_router.delete("/sciences/{sid}")
async def delete_science(sid: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    topic_ids = (await db.execute(select(Topic.id).where(Topic.science_id == sid))).scalars().all()
    if topic_ids:
        await db.execute(sql_delete(Note).where(Note.topic_id.in_(topic_ids)))
        await db.execute(sql_delete(MiscNote).where(MiscNote.topic_id.in_(topic_ids)))
    await db.execute(sql_delete(Topic).where(Topic.science_id == sid))
    await db.execute(sql_delete(Book).where(Book.science_id == sid))
    await db.execute(sql_delete(Science).where(Science.id == sid))
    await db.commit()
    return {"ok": True}


@api_router.post("/sciences/reorder")
async def reorder_sciences(data: ReorderIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    for i, sid in enumerate(data.ids):
        s = (await db.execute(select(Science).where(Science.id == sid))).scalar_one_or_none()
        if s:
            s.sort_order = i
    await db.commit()
    return {"ok": True}


# ---------------- Books ----------------
@api_router.get("/sciences/{sid}/books")
async def list_books(sid: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Book).where(Book.science_id == sid).order_by(Book.sort_order))).scalars().all()
    return [book_dict(b) for b in rows]


@api_router.post("/books")
async def create_book(data: BookIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    b = Book(id=uid(), science_id=data.science_id, title=data.title, arabic_title=data.arabic_title or "", author=data.author or "", difficulty=data.difficulty, tag=data.tag or "", sort_order=await _count(db, Book, science_id=data.science_id), created_at=now_iso())
    db.add(b)
    await db.commit()
    return book_dict(b)


@api_router.put("/books/{bid}")
async def update_book(bid: str, data: BookIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    b = (await db.execute(select(Book).where(Book.id == bid))).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Book not found")
    b.title = data.title
    b.arabic_title = data.arabic_title or ""
    b.author = data.author or ""
    b.difficulty = data.difficulty
    b.tag = data.tag or ""
    await db.commit()
    return book_dict(b)


@api_router.delete("/books/{bid}")
async def delete_book(bid: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(sql_delete(Note).where(Note.book_id == bid))
    await db.execute(sql_delete(Book).where(Book.id == bid))
    await db.commit()
    return {"ok": True}


@api_router.post("/books/reorder")
async def reorder_books(data: ReorderIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    for i, bid in enumerate(data.ids):
        b = (await db.execute(select(Book).where(Book.id == bid))).scalar_one_or_none()
        if b:
            b.sort_order = i
    await db.commit()
    return {"ok": True}


# ---------------- Topics ----------------
@api_router.get("/sciences/{sid}/topics")
async def list_topics(sid: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Topic).where(Topic.science_id == sid).order_by(Topic.sort_order))).scalars().all()
    return [topic_dict(t) for t in rows]


@api_router.get("/topics/{tid}")
async def get_topic(tid: str, db: AsyncSession = Depends(get_db)):
    t = (await db.execute(select(Topic).where(Topic.id == tid))).scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Topic not found")
    trail = []
    cur = t
    while cur:
        trail.insert(0, {"id": cur.id, "title": cur.title})
        if cur.parent_id:
            cur = (await db.execute(select(Topic).where(Topic.id == cur.parent_id))).scalar_one_or_none()
        else:
            cur = None
    out = topic_dict(t)
    out["breadcrumb"] = trail
    return out


@api_router.post("/topics")
async def create_topic(data: TopicIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    count = await _count(db, Topic, science_id=data.science_id, parent_id=data.parent_id)
    t = Topic(id=uid(), science_id=data.science_id, parent_id=data.parent_id, title=data.title, arabic_title=data.arabic_title or "", intro_html=data.intro_html or "", sort_order=count, created_at=now_iso())
    db.add(t)
    await db.commit()
    return topic_dict(t)


@api_router.put("/topics/{tid}")
async def update_topic(tid: str, data: TopicUpdate, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    t = (await db.execute(select(Topic).where(Topic.id == tid))).scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Topic not found")
    if data.title is not None:
        t.title = data.title
    if data.arabic_title is not None:
        t.arabic_title = data.arabic_title
    if data.intro_html is not None:
        t.intro_html = data.intro_html
    await db.commit()
    return topic_dict(t)


@api_router.delete("/topics/{tid}")
async def delete_topic(tid: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    ids = await collect_descendants(db, tid)
    await db.execute(sql_delete(Note).where(Note.topic_id.in_(ids)))
    await db.execute(sql_delete(MiscNote).where(MiscNote.topic_id.in_(ids)))
    await db.execute(sql_delete(Topic).where(Topic.id.in_(ids)))
    await db.commit()
    return {"ok": True}


@api_router.post("/topics/reorder")
async def reorder_topics(data: ReorderIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    for i, tid in enumerate(data.ids):
        t = (await db.execute(select(Topic).where(Topic.id == tid))).scalar_one_or_none()
        if t:
            t.sort_order = i
    await db.commit()
    return {"ok": True}


@api_router.put("/topics/{tid}/move")
async def move_topic(tid: str, data: MoveIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    t = (await db.execute(select(Topic).where(Topic.id == tid))).scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Topic not found")
    new_parent = data.parent_id or None
    if new_parent == tid:
        raise HTTPException(status_code=400, detail="Cannot move a topic into itself")
    if new_parent:
        if new_parent in await collect_descendants(db, tid):
            raise HTTPException(status_code=400, detail="Cannot move a topic into its own subtopic")
        parent = (await db.execute(select(Topic).where(Topic.id == new_parent))).scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=404, detail="Target parent not found")
    t.parent_id = new_parent
    await db.flush()
    stmt = select(Topic).where(Topic.science_id == t.science_id).order_by(Topic.sort_order)
    if new_parent is None:
        stmt = select(Topic).where(Topic.science_id == t.science_id, Topic.parent_id.is_(None)).order_by(Topic.sort_order)
    else:
        stmt = select(Topic).where(Topic.science_id == t.science_id, Topic.parent_id == new_parent).order_by(Topic.sort_order)
    siblings = (await db.execute(stmt)).scalars().all()
    ids = [s.id for s in siblings if s.id != tid]
    if data.before_id and data.before_id in ids:
        ids.insert(ids.index(data.before_id), tid)
    else:
        ids.append(tid)
    order_map = {sid: i for i, sid in enumerate(ids)}
    for s in siblings:
        if s.id in order_map:
            s.sort_order = order_map[s.id]
    t.sort_order = order_map.get(tid, len(ids) - 1)
    await db.commit()
    return {"ok": True}


# ---------------- Notes ----------------
@api_router.get("/topics/{tid}/notes")
async def list_notes(tid: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Note).where(Note.topic_id == tid).order_by(Note.sort_order))).scalars().all()
    return [note_dict(n) for n in rows]


@api_router.post("/notes")
async def create_note(data: NoteIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    n = Note(id=uid(), topic_id=data.topic_id, book_id=data.book_id, arabic_text=data.arabic_text or "", english_html=data.english_html or "", sort_order=await _count(db, Note, topic_id=data.topic_id), created_at=now_iso(), updated_at=now_iso())
    db.add(n)
    await db.commit()
    return note_dict(n)


@api_router.put("/notes/{nid}")
async def update_note(nid: str, data: NoteUpdate, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    n = (await db.execute(select(Note).where(Note.id == nid))).scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Note not found")
    if data.arabic_text is not None:
        n.arabic_text = data.arabic_text
    if data.english_html is not None:
        n.english_html = data.english_html
    n.updated_at = now_iso()
    await db.commit()
    return note_dict(n)


@api_router.delete("/notes/{nid}")
async def delete_note(nid: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(sql_delete(Note).where(Note.id == nid))
    await db.commit()
    return {"ok": True}


@api_router.post("/notes/reorder")
async def reorder_notes(data: ReorderIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    for i, nid in enumerate(data.ids):
        n = (await db.execute(select(Note).where(Note.id == nid))).scalar_one_or_none()
        if n:
            n.sort_order = i
    await db.commit()
    return {"ok": True}


# ---------------- Misc notes ----------------
@api_router.get("/topics/{tid}/miscnotes")
async def list_miscnotes(tid: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(MiscNote).where(MiscNote.topic_id == tid).order_by(MiscNote.sort_order))).scalars().all()
    return [misc_dict(m) for m in rows]


@api_router.post("/miscnotes")
async def create_miscnote(data: MiscNoteIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    m = MiscNote(id=uid(), topic_id=data.topic_id, title=data.title or "", content_html=data.content_html, sort_order=await _count(db, MiscNote, topic_id=data.topic_id), created_at=now_iso(), updated_at=now_iso())
    db.add(m)
    await db.commit()
    return misc_dict(m)


@api_router.put("/miscnotes/{mid}")
async def update_miscnote(mid: str, data: MiscNoteUpdate, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    m = (await db.execute(select(MiscNote).where(MiscNote.id == mid))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Note not found")
    if data.title is not None:
        m.title = data.title
    if data.content_html is not None:
        m.content_html = data.content_html
    m.updated_at = now_iso()
    await db.commit()
    return misc_dict(m)


@api_router.delete("/miscnotes/{mid}")
async def delete_miscnote(mid: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(sql_delete(MiscNote).where(MiscNote.id == mid))
    await db.commit()
    return {"ok": True}


# ---------------- Search ----------------
@api_router.get("/search")
async def search(q: str, science_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    q = (q or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    rx = re.compile(re.escape(q), re.IGNORECASE)
    results = []
    seen = set()

    tstmt = select(Topic).where(Topic.title.ilike(like) | Topic.arabic_title.ilike(like))
    if science_id:
        tstmt = tstmt.where(Topic.science_id == science_id)
    for t in (await db.execute(tstmt.limit(200))).scalars().all():
        key = ("topic", t.id)
        if key in seen:
            continue
        seen.add(key)
        results.append({"type": "topic", "topic_id": t.id, "science_id": t.science_id, "title": t.title, "snippet": t.arabic_title or ""})

    nstmt = select(Note).where(Note.english_html.ilike(like) | Note.arabic_text.ilike(like)).limit(400)
    for n in (await db.execute(nstmt)).scalars().all():
        plain = strip_html(n.english_html or "")
        if not (rx.search(plain) or rx.search(n.arabic_text or "")):
            continue
        t = (await db.execute(select(Topic).where(Topic.id == n.topic_id))).scalar_one_or_none()
        if not t:
            continue
        if science_id and t.science_id != science_id:
            continue
        b = (await db.execute(select(Book).where(Book.id == n.book_id))).scalar_one_or_none()
        text = plain or (n.arabic_text or "")
        m = rx.search(text)
        snippet = text
        if m:
            start = max(0, m.start() - 40)
            snippet = ("…" if start > 0 else "") + text[start:start + 160] + "…"
        key = ("note", n.id)
        if key in seen:
            continue
        seen.add(key)
        results.append({"type": "note", "topic_id": n.topic_id, "science_id": t.science_id, "title": t.title, "book_title": b.title if b else "", "snippet": snippet})
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
    async with SessionLocal() as db:
        existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if not existing:
            db.add(User(id=uid(), email=email, password_hash=hash_password(password), name="Admin", role="admin", created_at=now_iso()))
            await db.commit()
        elif not verify_password(password, existing.password_hash):
            existing.password_hash = hash_password(password)
            await db.commit()


async def seed_content():
    async with SessionLocal() as db:
        if await _count(db, Science) > 0:
            return
        sid = uid()
        db.add(Science(id=sid, name="Usul al-Fiqh", arabic_name="أصول الفقه", description="The principles of Islamic jurisprudence.", sort_order=0, created_at=now_iso()))

        books = [
            ("Matn al-Waraqat", "متن الورقات", "Imam al-Juwayni", "primer", "Shafi'i · 478 AH"),
            ("Lubb al-Usul", "لب الأصول", "Zakariyya al-Ansari", "intermediate", "Shafi'i · 926 AH"),
            ("Al-Mahsul", "المحصول", "Fakhr al-Din al-Razi", "advanced", "Shafi'i · 606 AH"),
        ]
        book_ids = []
        for i, (title, ar, author, diff, tag) in enumerate(books):
            bid = uid()
            book_ids.append(bid)
            db.add(Book(id=bid, science_id=sid, title=title, arabic_title=ar, author=author, difficulty=diff, tag=tag, sort_order=i, created_at=now_iso()))

        parent_id = uid()
        db.add(Topic(id=parent_id, science_id=sid, parent_id=None, title="Al-Adillah (The Evidences)", arabic_title="الأدلة", intro_html="<p>An overview of the sources of evidence in legal theory.</p>", sort_order=0, created_at=now_iso()))

        children = [
            ("Al-Khaas (The Specific)", "الخاص"),
            ("Al-Aam (The General)", "العام"),
            ("Al-Amr (The Command)", "الأمر"),
            ("Al-Nahy (The Prohibition)", "النهي"),
        ]
        for i, (title, ar) in enumerate(children):
            tid = uid()
            db.add(Topic(id=tid, science_id=sid, parent_id=parent_id, title=title, arabic_title=ar, intro_html="", sort_order=i, created_at=now_iso()))
            if i == 0:
                samples = [
                    ("العام هو اللفظ المستغرق لجميع ما يصلح له", "<p><strong>Al-Khaas</strong> is a term that indicates a <em>single, specific</em> meaning and does not extend to encompass many individuals. It is the opposite of al-'Aam (the general).</p><ul><li>A specific word designates one determinate meaning.</li><li>It acts decisively upon what it denotes.</li></ul>"),
                    ("الخاص قسيم العام", "<p>The author elaborates that al-Khaas is the counterpart of al-'Aam. Whereas the general word admits <em>takhsis</em> (specification), the specific word carries a definitive ruling upon its subject.</p>"),
                    ("", "<p>Al-Razi offers a detailed epistemological treatment, distinguishing the linguistic from the legal usage of specification.</p><blockquote>The specific yields decisive knowledge upon its referent unless a contextual indicator suggests otherwise.</blockquote>"),
                ]
                for j, (ar_txt, en) in enumerate(samples):
                    db.add(Note(id=uid(), topic_id=tid, book_id=book_ids[j], arabic_text=ar_txt, english_html=en, sort_order=j, created_at=now_iso(), updated_at=now_iso()))
                db.add(MiscNote(id=uid(), topic_id=tid, title="Study reminder", content_html="<p>Compare how the primer treats al-Khaas versus the depth in al-Mahsul.</p>", sort_order=0, created_at=now_iso(), updated_at=now_iso()))
        await db.commit()


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_admin()
    await seed_content()


@app.on_event("shutdown")
async def shutdown():
    await engine.dispose()
