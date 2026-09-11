import json
import pytest
from tests.test_hypotheses import _hypothesis, _prediction, _register
from tests.test_review_cli import _json, _thth

@pytest.mark.parametrize("prediction", [
    _prediction(window=None, scope=None),
    _prediction(statement="非フォロワー由来のviewsの24時間増分が増える", metrics=["views"], scope="非フォロワー由来のみ"),
    _prediction(statement="他人への返信回数が多いほど自分の投稿のviewsが伸びる", metrics=["views", "replies"]),
])
def test_shadow_requires_observable_design(thth_root, prediction):
    proc = _register(_hypothesis(state="shadow", predictions=[prediction]))
    print("SHADOW_INPUT", json.dumps(prediction, ensure_ascii=False))
    print("SHADOW_RESULT",proc.returncode,proc.stdout)
    listed=_json(_thth(["topics","hypotheses","--state","shadow"]))
    print("SHADOW_LIST",json.dumps(listed,ensure_ascii=False))
    assert proc.returncode != 0 or listed["count"] == 0
