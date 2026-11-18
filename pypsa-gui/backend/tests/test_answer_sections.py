"""Pure answer sections, their gate and their receipt contract (issue 10, S6a)."""

import json
import random

import pytest
from pydantic import ValidationError

from harness import answer_sections as sections
from harness.assurance_shapes import RECEIPTS

METRICS = ("dispatch_energy", "marginal_expenditure", "peak_dispatch")


def slots(names, metrics=METRICS, component_class="Generator"):
    return [
        {
            "component_class": component_class,
            "entity_id": n,
            "metric": m,
            "disposition": "answered",
        }
        for n in names
        for m in metrics
    ]


def names(count):
    return [f"G{i:02d}" for i in range(1, count + 1)]


def flat(result):
    return [
        (s["entity_id"], s["metric"]) for section in result for s in section["slots"]
    ]


# --- sectionise ---


def test_twenty_slots_fit_one_section_with_no_heading():
    result = sections.sectionise(slots(names(20), ("dispatch_energy",)), METRICS)
    assert [s["heading"] for s in result] == [None] and len(result[0]["slots"]) == 20


def test_twenty_one_slots_make_two_headed_sections():
    result = sections.sectionise(slots(names(21), ("dispatch_energy",)), METRICS)
    assert [s["heading"] for s in result] == [
        "Generators 1–20 of 21",
        "Generators 21 of 21",
    ]


def test_thirty_generators_read_like_the_ruling():
    result = sections.sectionise(slots(names(30), ("dispatch_energy",)), METRICS)
    assert [s["heading"] for s in result] == [
        "Generators 1–20 of 30",
        "Generators 21–30 of 30",
    ]


def test_a_member_split_across_sections_is_named_in_both():
    result = sections.sectionise(slots(names(10)), METRICS)
    assert [s["heading"] for s in result] == [
        "Generators 1–7 of 10",
        "Generators 7–10 of 10",
    ]
    assert [len(s["slots"]) for s in result] == [20, 10]


def test_each_slot_lands_in_exactly_one_section_in_type_entity_metric_order():
    given = slots(["b", "a"], ("peak_dispatch", "dispatch_energy")) + slots(
        ["L2", "L1"], ("m",), "Line"
    )
    result = sections.sectionise(given, METRICS + ("m",))
    assert flat(result) == [
        ("a", "dispatch_energy"),
        ("a", "peak_dispatch"),
        ("b", "dispatch_energy"),
        ("b", "peak_dispatch"),
        ("L1", "m"),
        ("L2", "m"),
    ]
    assert [s["heading"] for s in result] == ["Generators 1–2 of 2", "Lines 1–2 of 2"]


def test_sections_are_stable_under_permutation():
    given = slots(names(33))
    expected = sections.sectionise(given, METRICS)
    shuffled = given[:]
    random.Random(7).shuffle(shuffled)
    assert sections.sectionise(shuffled, METRICS) == expected


def test_a_rename_that_changes_the_sort_order_moves_the_entity():
    given = slots(names(21), ("dispatch_energy",))
    given[0]["entity_id"] = "Z99"  # G01 renamed: now last
    result = sections.sectionise(given, METRICS)
    assert result[0]["slots"][0]["entity_id"] == "G02"
    assert result[1]["slots"] == [given[0]]
    assert [s["heading"] for s in result] == [
        "Generators 1–20 of 21",
        "Generators 21 of 21",
    ]


def test_a_type_outside_the_plural_map_is_refused_never_shown_as_a_class_name():
    with pytest.raises(ValueError, match="unsupported_section_type"):
        sections.sectionise(slots(["S1"], ("m",), "StorageUnit"), ("m",))
    assert sections.PLURALS == {"Generator": "Generators", "Line": "Lines"}


def test_more_than_five_sections_is_refused():
    sections.sectionise(slots(names(100), ("dispatch_energy",)), METRICS)
    with pytest.raises(ValueError, match="too_many_values"):
        sections.sectionise(slots(names(101), ("dispatch_energy",)), METRICS)


# --- K4: the bound, counted before any calculation ---


@pytest.mark.parametrize(
    "entities, metrics, ok",
    [
        (20, 1, True),
        (7, 3, True),
        (100, 1, True),
        (50, 2, True),
        (101, 1, False),
        (34, 3, False),
    ],
)
def test_admit_bounds(entities, metrics, ok):
    if ok:
        assert sections.admit(entities, metrics) == entities * metrics
    else:
        with pytest.raises(ValueError) as refused:
            sections.admit(entities, metrics)
        assert refused.value.args == ("too_many_values", entities * metrics)


def test_the_refusal_sentence_is_pinned():
    assert sections.refusal(140) == (
        "I can check up to 100 values in one answer and this needs 140: "
        "ask for one carrier or one bus at a time."
    )


# --- K3: the gate ---


def answered(count):
    """`count` one-metric generators, each with its fact; values avoid 1, 20, 21 and 30."""
    given = slots(names(count), ("dispatch_energy",))
    for i, slot in enumerate(given):
        slot["fact"] = {
            "entity_id": slot["entity_id"],
            "metric": "dispatch_energy",
            "value": 1000 + i,
            "unit": "MWh",
            "formula_id": "generator-energy/1",
            "path": f"/{i}/dispatch_energy",
        }
    return sections.sectionise(given, METRICS)


def drafted(result):
    return [
        (
            s["heading"],
            [
                {
                    "id": f"b{s['fact']['path'][1:].split('/')[0]}",
                    "source": {"evidence_id": "E", "path": s["fact"]["path"]},
                    **{
                        k: s["fact"][k]
                        for k in ("entity_id", "metric", "value", "unit", "formula_id")
                    },
                }
                for s in s["slots"]
            ],
        )
        for s in result
    ]


def test_the_gate_publishes_each_section_and_ignores_heading_numbers():
    result = answered(30)
    published = sections.gate(drafted(result), result, "E")
    assert [block_id for block_id, _ in published] == [f"b{i}" for i in range(30)]
    values = {fact["value"] for _, fact in published}
    assert not values & {1, 20, 21, 30}  # heading numbers are text, never facts


def test_a_value_from_section_two_claimed_in_section_one_is_withheld():
    result = answered(30)
    draft = drafted(result)
    draft[0][1][0], draft[1][1][0] = draft[1][1][0], draft[0][1][0]
    with pytest.raises(ValueError, match="differs"):
        sections.gate(draft, result, "E")


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d[0][1][0].update(value=1001.5),
        lambda d: d[1][1][0]["source"].update(evidence_id="F"),
        lambda d: d[1][1][0]["source"].update(path="/0/dispatch_energy"),
        lambda d: d[1][1][0]["source"].update(tool_use_id="t"),
        lambda d: d[1][1].append(dict(d[1][1][0])),
        lambda d: d[1][1].pop(),
        lambda d: d.pop(),
        lambda d: d.__setitem__(0, ("Generators 1–21 of 30", d[0][1])),
    ],
)
def test_any_other_drift_is_withheld(change):
    result = answered(30)
    draft = drafted(result)
    for _, blocks in draft:
        for block in blocks:
            block["source"].setdefault("tool_use_id", None)
    change(draft)
    with pytest.raises(ValueError):
        sections.gate(draft, result, "E")


def test_a_duplicate_claim_in_place_of_another_is_withheld():
    result = answered(30)
    draft = drafted(result)
    draft[1][1][1] = dict(draft[1][1][0])
    with pytest.raises(ValueError, match="differs"):
        sections.gate(draft, result, "E")


def test_record_sections_join_slots_to_the_recorded_entities():
    record = {
        "raw": {"entities": ["a", "b"], "component_class": "Line"},
        "slots": [
            {"metric": "m", "disposition": "answered"},
            {"metric": "m", "disposition": "unavailable", "reason": "r"},
        ],
        "facts": [
            {
                "path": "/0/m",
                "entity_id": "a",
                "metric": "m",
                "value": 3,
                "unit": "%",
                "formula_id": "f",
            }
        ],
    }
    (only,) = sections.record_sections(record, ["m"])
    assert only["heading"] is None
    assert [(s["entity_id"], "fact" in s) for s in only["slots"]] == [
        ("a", True),
        ("b", False),
    ]
    with pytest.raises(ValueError, match="population_slots_differ"):
        sections.record_sections({**record, "slots": record["slots"][:1]}, ["m"])
    with pytest.raises(ValueError, match="population_slots_differ"):
        sections.record_sections(record, ["n"])
    with pytest.raises(
        ValueError, match="population_slots_differ"
    ):  # two metrics need four slots
        sections.record_sections(record, ["m", "n"])
    two = {
        **record,
        "slots": [{"metric": m, "disposition": "answered"} for _ in "ab" for m in "mn"],
    }
    (both,) = sections.record_sections(two, ["m", "n"])  # two metrics, four slots
    assert [(s["entity_id"], s["metric"]) for s in both["slots"]] == [
        ("a", "m"),
        ("a", "n"),
        ("b", "m"),
        ("b", "n"),
    ]


# --- K5: the receipt contract, in code and in the schema JSON ---


def receipt(section_sizes, heading="Generators 1–20 of 100"):
    return {
        "policy_version": "4",
        "content_sha256": "0" * 64,
        "evidence_ids": ["e"],
        "checked_block_ids": [f"b{i}" for i in range(sum(section_sizes))],
        "limitations": [],
        "reviewer_status": "not_required",
        "reviewed_claim_ids": [],
        "sections": [
            {
                "heading": heading,
                "result_slots": [
                    {
                        "metric": "m",
                        "disposition": "answered",
                        "entity_id": f"G{i}",
                        "component_class": "Generator",
                    }
                    for i in range(size)
                ],
            }
            for size in section_sizes
        ],
    }


def test_five_full_sections_hold_a_hundred_slots():
    RECEIPTS["4"].model_validate(receipt([20] * 5))


@pytest.mark.parametrize("sizes", [[20] * 6, [21], [20, 20, 20, 20, 21], []])
def test_the_receipt_refuses_more_than_its_bounds(sizes):
    with pytest.raises(ValidationError):
        RECEIPTS["4"].model_validate(receipt(sizes))


def test_the_receipt_refuses_a_long_heading_and_a_slot_without_its_entity():
    with pytest.raises(ValidationError):
        RECEIPTS["4"].model_validate(receipt([1], heading="x" * 65))
    bare = receipt([1])
    del bare["sections"][0]["result_slots"][0]["entity_id"]
    with pytest.raises(ValidationError):
        RECEIPTS["4"].model_validate(bare)


def test_old_receipts_without_sections_still_validate():
    old = {k: v for k, v in receipt([1]).items() if k != "sections"}
    RECEIPTS["1"].model_validate(
        {
            **old,
            "policy_version": "1",
            "checked_block_ids": [],
            "result_slots": [{"metric": "m", "disposition": "answered"}],
        }
    )
    with pytest.raises(ValidationError):  # sections belong to policy "4" only
        RECEIPTS["1"].model_validate({**receipt([1]), "policy_version": "1"})


def test_the_schema_json_carries_the_same_bounds():
    from harness.assurance_contracts import SNAPSHOT

    schema = json.loads(SNAPSHOT.read_text())["answer_receipt"]["4"]
    assert schema["properties"]["sections"]["maxItems"] == 5
    assert schema["properties"]["checked_block_ids"]["maxItems"] == 100
    section = schema["$defs"]["Section"]["properties"]
    assert section["result_slots"]["maxItems"] == 20
    assert section["heading"]["anyOf"][0]["maxLength"] == 64
    draft = json.loads(SNAPSHOT.read_text())["computed_draft"]
    assert draft["properties"]["sections"]["anyOf"][0]["maxItems"] == 5
    assert draft["$defs"]["DraftSection"]["properties"]["blocks"]["maxItems"] == 20


def test_policy_four_is_checked_when_an_answer_is_read():
    from services.assurance_records import receipt_policy_ok

    assert receipt_policy_ok(receipt([20, 3]))
    assert not receipt_policy_ok(receipt([21]))
    assert not receipt_policy_ok({**receipt([1]), "result_slots": []})
