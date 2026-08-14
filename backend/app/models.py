import datetime as dt

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, text
from sqlalchemy.orm import relationship

from .db import Base


def _now():
    return dt.datetime.now(dt.UTC)


class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    users = relationship("User", back_populates="org")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index(
            "uq_users_single_admin_per_org",
            "org_id",
            unique=True,
            sqlite_where=text("role = 'admin'"),
            postgresql_where=text("role = 'admin'"),
        ),
    )
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    email = Column(String(120), nullable=False, unique=True)
    password_hash = Column(String(128), nullable=False)
    role = Column(String(20), default="member")  # admin | member
    created_at = Column(DateTime(timezone=True), default=_now)
    org = relationship("Organization", back_populates="users")


class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    address = Column(String(200), default="")
    created_at = Column(DateTime(timezone=True), default=_now)
    scans = relationship("Scan", back_populates="project", cascade="all, delete-orphan")


class Scan(Base):
    __tablename__ = "scans"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    status = Column(String(20), default="uploading")
    progress = Column(Integer, default=0)
    message = Column(String(200), default="")
    capture_type = Column(String(20), default="video")  # video | photos
    media_path = Column(String(300), default="")
    reference_measurements = Column(JSON, default=list, nullable=False, server_default=text("'[]'"))
    created_at = Column(DateTime(timezone=True), default=_now)
    project = relationship("Project", back_populates="scans")
    report = relationship(
        "Report",
        back_populates="scan",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Report(Base):
    __tablename__ = "reports"
    id = Column(Integer, primary_key=True)
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=False, unique=True)
    # 注意：不能给 default=0 —— SQLAlchemy 的 Python 侧 default 在值为 None 时
    # 也会触发，会把「无法评分」（score=None）错误地落库为 0 分。
    score = Column(Float, nullable=True)
    risks = Column(JSON, default=list)
    measures = Column(JSON, default=dict)
    advice = Column(JSON, default=list)
    images = Column(JSON, default=list)
    preview = Column(JSON, default=dict)
    calibrated = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=_now)
    scan = relationship("Scan", back_populates="report")
