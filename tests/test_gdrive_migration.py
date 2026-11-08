import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_gdrive_task_migrations_upgrade_and_downgrade(monkeypatch) -> None:
    """GDrive 任务表迁移应支持幂等升级和完整回滚。"""
    base_migration = importlib.import_module("database.versions.c7e4f1a2b9d0_gdrive_task")
    lease_migration = importlib.import_module("database.versions.d91b7e42a6c3_gdrive_task_lease")
    history_migration = importlib.import_module(
        "database.versions.e4b8c9d0f1a2_gdrive_task_history"
    )
    engine = sa.create_engine("sqlite://")

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(base_migration, "op", Operations(context))
        monkeypatch.setattr(lease_migration, "op", Operations(context))
        monkeypatch.setattr(history_migration, "op", Operations(context))

        base_migration.upgrade()
        base_migration.upgrade()
        lease_migration.upgrade()
        lease_migration.upgrade()
        history_migration.upgrade()
        history_migration.upgrade()

        history_table = sa.Table("gdrivetaskhistory", sa.MetaData(), autoload_with=connection)
        assert {"task_id", "to_status", "to_stage", "event_type", "error_message"} <= {
            column.name for column in history_table.columns
        }

        table = sa.Table("gdrivetask", sa.MetaData(), autoload_with=connection)
        assert {"lease_owner", "lease_until", "source_device", "source_inode", "source_sha256"} <= {
            column.name for column in table.columns
        }

        lease_migration.downgrade()
        table = sa.Table("gdrivetask", sa.MetaData(), autoload_with=connection)
        assert not {"lease_owner", "lease_until", "source_device", "source_inode", "source_sha256"} & {
            column.name for column in table.columns
        }

        history_migration.downgrade()
        assert "gdrivetaskhistory" not in sa.inspect(connection).get_table_names()
        base_migration.downgrade()
        assert "gdrivetask" not in sa.inspect(connection).get_table_names()
