"""Full pipeline on the real engines (GPU Whisper + CPU sherpa). Run with: pytest -m gpu"""

import pytest

from scribe.bench.run import bench_e2e, load_fixtures

pytestmark = [pytest.mark.gpu, pytest.mark.slow]


def test_full_pipeline_fixture_replay():
    res = bench_e2e(load_fixtures(["meeting_tech"]), realtime=False)
    assert res["sentences_with_final"] == res["sentences_total"]
    assert res["sentences_with_partial"] == res["sentences_total"]
    assert res["cer"] <= 0.12, res
