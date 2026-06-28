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
    UniqueConstraint,
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
    # dedupは (tx_hash, cluster_id) 複合。1txが2クラスタに跨る正当な cross-ETF を許容しつつ、
    # 同一クラスタの二重発火は防ぐ。
    __table_args__ = (UniqueConstraint("tx_hash", "cluster_id", name="uq_tx_cluster"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    sent_at = Column(DateTime, nullable=False, index=True)
    cluster_id = Column(String(64), nullable=False)
    tx_hash = Column(String(128), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    direction = Column(String(8), nullable=False)


_engine = create_engine(f"sqlite:///{DB_PATH}", future=True)


def _migrate_alert_logs_unique() -> None:
    """旧スキーマ(tx_hash 単独UNIQUE)を (tx_hash, cluster_id) 複合へ移行。

    旧UNIQUE(tx_hash)があると cross-ETF の2クラスタ目insertがIntegrityErrorで弾かれる。
    alert_logsは10分窓のdedup用途のみ(分析データではない)ので、旧スキーマ検出時は
    drop→再作成で安全に移行する。flow_snapshots等には触れない。
    """
    with _engine.begin() as conn:
        tables = [r[0] for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_logs'"
        ).fetchall()]
        if not tables:
            return  # まだ無い → create_all で新スキーマ作成される
        for idx in conn.exec_driver_sql("PRAGMA index_list('alert_logs')").fetchall():
            idx_name, unique = idx[1], idx[2]
            if not unique:
                continue
            cols = [c[2] for c in conn.exec_driver_sql(f"PRAGMA index_info('{idx_name}')").fetchall()]
            if cols == ["tx_hash"]:  # 旧スキーマ
                conn.exec_driver_sql("DROP TABLE alert_logs")
                return


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(_engine)  # 無いテーブルを作成
    _migrate_alert_logs_unique()       # 旧alert_logsがあれば drop
    Base.metadata.create_all(_engine)  # drop後に新スキーマで再作成


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


def is_alert_already_sent(tx_hash: str, cluster_id: str) -> bool:
    with Session(_engine) as session:
        stmt = (
            select(func.count())
            .select_from(AlertLog)
            .where(AlertLog.tx_hash == tx_hash)
            .where(AlertLog.cluster_id == cluster_id)
        )
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
