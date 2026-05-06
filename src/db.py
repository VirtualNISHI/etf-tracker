"""フロー履歴の永続化(SQLite)。"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Session

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "history.sqlite"


class Base(DeclarativeBase):
    pass


class FlowSnapshot(Base):
    """24時間集計のスナップショット(クラスタ単位)。"""

    __tablename__ = "flow_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    captured_at = Column(DateTime, nullable=False, index=True)
    cluster_id = Column(String(64), nullable=False, index=True)
    chain = Column(String(16), nullable=False)
    inflow = Column(Float, nullable=False)
    outflow = Column(Float, nullable=False)
    net_flow = Column(Float, nullable=False)
    tx_count = Column(Integer, nullable=False, default=0)


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sent_at = Column(DateTime, nullable=False, index=True)
    cluster_id = Column(String(64), nullable=False)
    tx_hash = Column(String(128), nullable=False, unique=True)
    amount = Column(Float, nullable=False)
    direction = Column(String(8), nullable=False)


_engine = create_engine(f"sqlite:///{DB_PATH}", future=True)


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(_engine)


def record_snapshot(
    captured_at: datetime,
    cluster_id: str,
    chain: str,
    inflow: float,
    outflow: float,
    net_flow: float,
    tx_count: int,
) -> None:
    with Session(_engine) as session:
        session.add(
            FlowSnapshot(
                captured_at=captured_at,
                cluster_id=cluster_id,
                chain=chain,
                inflow=inflow,
                outflow=outflow,
                net_flow=net_flow,
                tx_count=tx_count,
            )
        )
        session.commit()


def is_alert_already_sent(tx_hash: str) -> bool:
    with Session(_engine) as session:
        stmt = select(func.count()).select_from(AlertLog).where(AlertLog.tx_hash == tx_hash)
        return (session.execute(stmt).scalar() or 0) > 0


def record_alert(cluster_id: str, tx_hash: str, amount: float, direction: str) -> None:
    with Session(_engine) as session:
        session.add(
            AlertLog(
                sent_at=datetime.utcnow(),
                cluster_id=cluster_id,
                tx_hash=tx_hash,
                amount=amount,
                direction=direction,
            )
        )
        session.commit()


def get_recent_snapshots(cluster_id: str, days: int) -> list[FlowSnapshot]:
    cutoff = datetime.utcnow() - timedelta(days=days)
    with Session(_engine) as session:
        stmt = (
            select(FlowSnapshot)
            .where(FlowSnapshot.cluster_id == cluster_id)
            .where(FlowSnapshot.captured_at >= cutoff)
            .order_by(FlowSnapshot.captured_at.asc())
        )
        return list(session.execute(stmt).scalars().all())
