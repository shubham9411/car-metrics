"""
Car Metrics — Sync Engine
Pluggable remote sync. Currently local-only with Supabase hooks ready.
Enable by setting CM_SYNC_ENABLED=true and providing Supabase credentials.
"""

import asyncio
import logging
import os
import time

import config
from storage import db

logger = logging.getLogger("storage.sync")

SYNC_TABLES = ["imu_readings", "gps_fixes", "obd_readings", "events"]


class SyncEngine:
    """Periodic sync of unsynced data to remote backend."""

    def __init__(self):
        self._running = False
        self._supabase = None

    def _init_remote(self):
        """Initialize Supabase client if credentials are configured."""
        if not config.SUPABASE_URL or not config.SUPABASE_KEY:
            logger.info("Supabase not configured — sync will only mark rows")
            return

        try:
            from supabase import create_client

            self._supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
            logger.info("Supabase client initialized: %s", config.SUPABASE_URL)
        except ImportError:
            logger.warning("supabase-py not installed — remote sync disabled")
        except Exception as e:
            logger.error("Supabase init error: %s", e)

    async def run(self):
        """Async sync loop — runs every SYNC_INTERVAL_SEC."""
        if not config.SYNC_ENABLED:
            logger.info("Sync disabled (set CM_SYNC_ENABLED=true to enable)")
            return

        self._init_remote()
        self._running = True
        logger.info("Sync engine started (interval=%ds)", config.SYNC_INTERVAL_SEC)

        while self._running:
            try:
                await self._sync_cycle()
            except Exception as e:
                logger.error("Sync cycle error: %s", e)

            await asyncio.sleep(config.SYNC_INTERVAL_SEC)

    async def _sync_cycle(self):
        """Perform one sync cycle — upload unsynced data and images."""
        for table in SYNC_TABLES:
            rows = db.get_unsynced_rows(table, limit=200)
            if not rows:
                continue

            row_ids = [row["id"] for row in rows]
            row_dicts = [dict(row) for row in rows]

            # Upload to Supabase if connected
            if self._supabase:
                try:
                    # Remove 'synced' field before upload
                    for rd in row_dicts:
                        rd.pop("synced", None)

                    self._supabase.table(table).insert(row_dicts).execute()
                    logger.debug("Synced %d rows to %s", len(row_ids), table)
                except Exception as e:
                    logger.warning("Failed to sync %s: %s", table, e)
                    continue  # Don't mark as synced if upload failed

            # Mark as synced
            db.mark_synced(table, row_ids)

        # Sync images
        await self._sync_images()

    async def _sync_images(self):
        """Upload unsynced camera frames to Supabase Storage."""
        rows = db.get_unsynced_rows("camera_frames", limit=20)
        if not rows:
            return

        for row in rows:
            filename = row["filename"]
            filepath = os.path.join(config.IMAGE_DIR, filename)

            if not os.path.exists(filepath):
                # File already rotated — just mark synced
                db.mark_synced("camera_frames", [row["id"]])
                continue

            if self._supabase:
                try:
                    with open(filepath, "rb") as f:
                        self._supabase.storage.from_(config.SUPABASE_BUCKET).upload(
                            path=filename,
                            file=f,
                            file_options={"content-type": "image/jpeg"},
                        )
                    logger.debug("Uploaded image: %s", filename)
                except Exception as e:
                    logger.warning("Image upload failed (%s): %s", filename, e)
                    continue

            db.mark_synced("camera_frames", [row["id"]])

    def stop(self):
        """Stop sync engine."""
        self._running = False
        logger.info("Sync engine stopped")
