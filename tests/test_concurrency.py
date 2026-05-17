"""Concurrency tests: async client deduplication and thread-safe DB writes."""

from __future__ import annotations

import asyncio
import threading


class TestParallelExecution:
    """Tests for concurrent access patterns in the pipeline."""

    def test_client_lock_prevents_duplicate_creation(self, monkeypatch):
        """Calling _get_ra_client() concurrently from 10 async tasks
        should only create one httpx.AsyncClient instance."""
        from cuepoint import event_fetcher as ef

        # Reset global client state
        monkeypatch.setattr(ef, "_ra_client", None)
        # Replace the module-level lock with a fresh one so there is no
        # stale lock from a previous event loop.
        monkeypatch.setattr(ef, "_ra_client_lock", asyncio.Lock())

        clients_seen: list[int] = []

        async def _gather_clients():
            tasks = [ef._get_ra_client() for _ in range(10)]
            results = await asyncio.gather(*tasks)
            # Collect object ids
            clients_seen.extend(id(c) for c in results)
            # Cleanup
            for c in set(results):
                await c.aclose()

        asyncio.run(_gather_clients())

        # All 10 calls should have returned the exact same client object
        unique_ids = set(clients_seen)
        assert len(unique_ids) == 1, (
            f"Expected 1 unique client, got {len(unique_ids)}. The asyncio.Lock should prevent duplicate creation."
        )

    def test_record_found_thread_safety(self, tmp_db):
        """Calling record_found() from multiple threads simultaneously
        should persist all lines without errors or data loss."""
        from cuepoint import db as store

        # Reset the in-memory found cache so it gets rebuilt from the tmp_db
        store._found_cache = None

        num_threads = 10
        lines_per_thread = 20
        barrier = threading.Barrier(num_threads)
        errors: list[str] = []

        def _worker(thread_idx: int) -> None:
            try:
                # Wait for all threads to be ready
                barrier.wait(timeout=5)
                for i in range(lines_per_thread):
                    line = f"city,2026-01-01,evt-{thread_idx}-{i},club,promo,artist-{thread_idx}-{i}"
                    store.record_found(line)
            except Exception as e:
                errors.append(f"Thread {thread_idx}: {e}")
            finally:
                store.close_db()

        threads = [threading.Thread(target=_worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Thread errors: {errors}"

        # Verify all lines were persisted
        all_lines = store.get_all_found_lines()
        expected_count = num_threads * lines_per_thread
        assert len(all_lines) == expected_count, (
            f"Expected {expected_count} lines, got {len(all_lines)}. "
            "Some writes may have been lost under concurrent access."
        )

        # Verify specific lines exist
        for t in range(num_threads):
            for i in range(lines_per_thread):
                expected = f"city,2026-01-01,evt-{t}-{i},club,promo,artist-{t}-{i}"
                assert expected in all_lines, f"Missing line: {expected}"
