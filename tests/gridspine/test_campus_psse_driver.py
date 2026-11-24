"""
The campus study passes the plant and the model set to its PSS/E bundles (GC-04d, GK A2 Q1-Q5).

A run directory is written by hand (``write_run``), so the driver is tested
without a solve. Oracles:
* with a plant and the run's own model set, every island hour's pack holds
  REPCA1, one psspy script per test-battery case and the citation;
* without them, every file ``psse_campus`` writes matches literal digests
  recorded from master 0fba5e80, and no other file is written;
* a model set that is not the run's campus, or that cannot be checked, is
  refused before anything is written; a refusal keeps an earlier ``psse/``
  and a new call replaces it whole.
"""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from gridspine.drivers import campus_study as D
from gridspine.handoff.dyr_writer import PocBranch
from gridspine.handoff.psse_pack import SUBDIR, Plant
from gridspine.schema.contracts import ContractError
from gridspine.schema.model_set import new_model_set
from tests.gridspine.test_campus_bundle import SCRIPTS
from tests.gridspine.test_campus_island_frequency_driver import campus_spec, write_run

THREE = (
    (0, "max_island_dp_up"),
    (1, "max_import"),
    (2, "min_stored_energy"),
)  # island hours 0 and 2
PLANT = Plant("PV1", PocBranch("trafo", 0, 0))  # TR1, grid end at the PCC

#: Every file ``psse_campus`` writes for ``_run`` with no plant or model set,
#: sha256, recorded from master 0fba5e80 (before GC-04d).
MASTER_SHA256 = {
    "TEST_HUB_p2030_h0/README.md": "dbb7509e37628a6016f374ac850bdc723709de2fd5a97328d82e0666031d7c05",
    "TEST_HUB_p2030_h0/TEST_HUB_p2030_h0.dyr": "605fa8911ebb98c5942d74069172afbf01713af3ca135f626845b206091923c4",
    "TEST_HUB_p2030_h0/TEST_HUB_p2030_h0.raw": "86b2d3133bc332049baf4c5225a58c3b1ebf3cba31219f55b6703b9bd14f3d2f",
    "TEST_HUB_p2030_h0/emt/README.md": "fb4939ffe8a47faf49f4ad8f9379610771fe0d4c1a09b3794659d3c527ce8995",
    "TEST_HUB_p2030_h0/emt/RSCAD_NOTES.md": "be12376e26799425ed992cfa063df31874849a655a581168f6fe4b1aabf3c46f",
    "TEST_HUB_p2030_h0/emt/cables.csv": "ab71639d60c2f1f9ef6c5e6b74022ebe8170a14a91269250f7b692ba53fb12bf",
    "TEST_HUB_p2030_h0/emt/converters.csv": "7750448f4191c420c9029c9de12c6e073df27568eb69f0f30753692255c1d6db",
    "TEST_HUB_p2030_h0/emt/earthing_transformers.csv": "c37f283c8998522b5f445ef65a77a0820cb8db13c1b7c9a8569053748f2cb051",
    "TEST_HUB_p2030_h0/emt/filters.csv": "dea3c8e1b304894569137c8b04bdeed1163c841bbab0b2a3c1d8076b3b0c885d",
    "TEST_HUB_p2030_h0/emt/gensets.csv": "c3f9f15688f2c0b527af561171aacf8ef9b2732dde1debddcb84554441a6bb8e",
    "TEST_HUB_p2030_h0/emt/insulation.csv": "28545e4d1ba616b621789a96554cdf91a8d23e555a4dbb6134665d9b47a3e940",
    "TEST_HUB_p2030_h0/emt/manifest.json": "5570c3f2b819f0747ffb72e824cec7a58c16c531501a327363dce6634809e400",
    "TEST_HUB_p2030_h0/emt/plant_units.csv": "0d7d180157832d0f44c7972d7dd532daf9bfc1ed5972d463e0e47e3b0dda87e3",
    "TEST_HUB_p2030_h0/emt/protection_relays.csv": "8146034b47da26b89b6e1dc7b7417c18cec06ca47301fa815b86a56be9330cc5",
    "TEST_HUB_p2030_h0/emt/thevenin_set.csv": "f6cde30356a138b0b6edeb34de8b9bbfb02fbfaef349f2a43017c0c9e19792d5",
    "TEST_HUB_p2030_h0/emt/thevenin_set.json": "533a775cc47d37c4a7b213bf529360609045ec2333bb8492d854711b3857a8d2",
    "TEST_HUB_p2030_h0/emt/thevenin_stages.csv": "bf2bef4209441e202a650c3cbf0c6fb890be191746d2f94007fe645aef3c1365",
    "TEST_HUB_p2030_h0/emt/to_be_supplied.csv": "9d64ef1e3933d5949609c53a20c10846d5fe84932e06e7214e872c1e46fd6ab8",
    "TEST_HUB_p2030_h0/emt/transformers.csv": "4c884604346c64d7008be9d76cb52a2d88a80e02173c8c98700c10b79eb2abef",
    "TEST_HUB_p2030_h0/gfm_models.csv": "bac9e6eabd15162aba26ea15de2cf27d491c150b9b20127dec64e1c743f86077",
    "TEST_HUB_p2030_h0/manifest.json": "78d36b725e0954ee125c34095b8513664826607ce4d83b2ec19121f6547e608e",
    "TEST_HUB_p2030_h0/psse_v35/README.md": "3472997a50b4342250491dffccf75e7f807882aa3942591e3f19189a27e9ede9",
    "TEST_HUB_p2030_h0/psse_v35/TEST_HUB_p2030_h0.con": "a29a2f212b370b74305ebf070f8b0c24f0de0cbf6aea9797a3f39004e1d9c49b",
    "TEST_HUB_p2030_h0/psse_v35/TEST_HUB_p2030_h0.conmap.csv": "6ee3d7f208adb5eb9ca99a909be4fcd03ccff4851fb194c8143689945eb49a3d",
    "TEST_HUB_p2030_h0/psse_v35/TEST_HUB_p2030_h0.dyr": "1f7c7882464baa79aa2d4032d6cbf726c8ef9b22ac0e29711a03d729f9c67f61",
    "TEST_HUB_p2030_h0/psse_v35/TEST_HUB_p2030_h0.mon": "919bd3668f0ae9dad850c9ffe65d3d901f8eb256abd78b4240533187276322d2",
    "TEST_HUB_p2030_h0/psse_v35/TEST_HUB_p2030_h0.raw": "029169314605de53508a0878486515acda92427122cb2cdbf8e6c1297cb39393",
    "TEST_HUB_p2030_h0/psse_v35/TEST_HUB_p2030_h0.seq": "e92ca7eac5e470b7080a4995ed37f1231f96abc11251b62b84ddd7e164f057b0",
    "TEST_HUB_p2030_h0/psse_v35/TEST_HUB_p2030_h0.sub": "723fb87c92a186e6a0e78dd08f54e44e41f01f2a446eb54161cc8d3b595139b4",
    "TEST_HUB_p2030_h0/psse_v35/manifest.json": "768631084d187f7aac124a260467cfb939cdb5c4f62317f4d03a4df3ee605671",
    "TEST_HUB_p2030_h2/README.md": "4def40a76742373413f15e37fa999168fca4d66c382e5a1f190116d84f8ae010",
    "TEST_HUB_p2030_h2/TEST_HUB_p2030_h2.dyr": "605fa8911ebb98c5942d74069172afbf01713af3ca135f626845b206091923c4",
    "TEST_HUB_p2030_h2/TEST_HUB_p2030_h2.raw": "0ef76ab523c2cca01f59de8bf7ffb480444635952e81db9d89f8264369b6f373",
    "TEST_HUB_p2030_h2/emt/README.md": "fb4939ffe8a47faf49f4ad8f9379610771fe0d4c1a09b3794659d3c527ce8995",
    "TEST_HUB_p2030_h2/emt/RSCAD_NOTES.md": "be12376e26799425ed992cfa063df31874849a655a581168f6fe4b1aabf3c46f",
    "TEST_HUB_p2030_h2/emt/cables.csv": "ab71639d60c2f1f9ef6c5e6b74022ebe8170a14a91269250f7b692ba53fb12bf",
    "TEST_HUB_p2030_h2/emt/converters.csv": "7750448f4191c420c9029c9de12c6e073df27568eb69f0f30753692255c1d6db",
    "TEST_HUB_p2030_h2/emt/earthing_transformers.csv": "c37f283c8998522b5f445ef65a77a0820cb8db13c1b7c9a8569053748f2cb051",
    "TEST_HUB_p2030_h2/emt/filters.csv": "dea3c8e1b304894569137c8b04bdeed1163c841bbab0b2a3c1d8076b3b0c885d",
    "TEST_HUB_p2030_h2/emt/gensets.csv": "c3f9f15688f2c0b527af561171aacf8ef9b2732dde1debddcb84554441a6bb8e",
    "TEST_HUB_p2030_h2/emt/insulation.csv": "28545e4d1ba616b621789a96554cdf91a8d23e555a4dbb6134665d9b47a3e940",
    "TEST_HUB_p2030_h2/emt/manifest.json": "5570c3f2b819f0747ffb72e824cec7a58c16c531501a327363dce6634809e400",
    "TEST_HUB_p2030_h2/emt/plant_units.csv": "0d7d180157832d0f44c7972d7dd532daf9bfc1ed5972d463e0e47e3b0dda87e3",
    "TEST_HUB_p2030_h2/emt/protection_relays.csv": "8146034b47da26b89b6e1dc7b7417c18cec06ca47301fa815b86a56be9330cc5",
    "TEST_HUB_p2030_h2/emt/thevenin_set.csv": "f6cde30356a138b0b6edeb34de8b9bbfb02fbfaef349f2a43017c0c9e19792d5",
    "TEST_HUB_p2030_h2/emt/thevenin_set.json": "533a775cc47d37c4a7b213bf529360609045ec2333bb8492d854711b3857a8d2",
    "TEST_HUB_p2030_h2/emt/thevenin_stages.csv": "bf2bef4209441e202a650c3cbf0c6fb890be191746d2f94007fe645aef3c1365",
    "TEST_HUB_p2030_h2/emt/to_be_supplied.csv": "9d64ef1e3933d5949609c53a20c10846d5fe84932e06e7214e872c1e46fd6ab8",
    "TEST_HUB_p2030_h2/emt/transformers.csv": "4c884604346c64d7008be9d76cb52a2d88a80e02173c8c98700c10b79eb2abef",
    "TEST_HUB_p2030_h2/gfm_models.csv": "bac9e6eabd15162aba26ea15de2cf27d491c150b9b20127dec64e1c743f86077",
    "TEST_HUB_p2030_h2/manifest.json": "0564725334fcbe092f3d123f58543850e9303c466767b7112e649ef84e4a8597",
    "TEST_HUB_p2030_h2/psse_v35/README.md": "e82406997f66ce6aae56c8486e1e1a106fd727831329416426cc2a47e5d94441",
    "TEST_HUB_p2030_h2/psse_v35/TEST_HUB_p2030_h2.con": "4f0eb08ab7f472a9b4bb2606fb6aa2de0f4f3179019c1bde332e714c80cd5dc7",
    "TEST_HUB_p2030_h2/psse_v35/TEST_HUB_p2030_h2.conmap.csv": "6ee3d7f208adb5eb9ca99a909be4fcd03ccff4851fb194c8143689945eb49a3d",
    "TEST_HUB_p2030_h2/psse_v35/TEST_HUB_p2030_h2.dyr": "1f7c7882464baa79aa2d4032d6cbf726c8ef9b22ac0e29711a03d729f9c67f61",
    "TEST_HUB_p2030_h2/psse_v35/TEST_HUB_p2030_h2.mon": "919bd3668f0ae9dad850c9ffe65d3d901f8eb256abd78b4240533187276322d2",
    "TEST_HUB_p2030_h2/psse_v35/TEST_HUB_p2030_h2.raw": "35381d8cfeed9b18716debd6d40cdd6eb364e958b5479f585856c066a24e5641",
    "TEST_HUB_p2030_h2/psse_v35/TEST_HUB_p2030_h2.seq": "e92ca7eac5e470b7080a4995ed37f1231f96abc11251b62b84ddd7e164f057b0",
    "TEST_HUB_p2030_h2/psse_v35/TEST_HUB_p2030_h2.sub": "723fb87c92a186e6a0e78dd08f54e44e41f01f2a446eb54161cc8d3b595139b4",
    "TEST_HUB_p2030_h2/psse_v35/manifest.json": "5891a40f7781c8556123ba20d38134ceb6a801b1b2a5b89a130532955e6ea5d7",
}


def _run(tmp_path):
    return write_run(tmp_path / "run", campus_spec(), selected=THREE)


def _model_set(s=None):
    import datetime

    return new_model_set(
        campus_spec() if s is None else s,
        {"study": "GC-04d"},
        now=datetime.datetime(2026, 11, 24, tzinfo=datetime.UTC),
    )


def _digests(out):
    return {
        p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(out.rglob("*"))
        if p.is_file()
    }


def _psse_entries(run):
    """``psse/`` and every hidden ``.psse*`` sibling in the run directory (GK A2 K5)."""
    return sorted(
        p.name
        for p in run.iterdir()
        if p.name.startswith((D.PSSE_DIR, f".{D.PSSE_DIR}"))
    )


def test_without_a_plant_or_model_set_every_file_matches_master(tmp_path):
    run = _run(tmp_path)
    D.psse_campus(run)
    assert (
        _digests(run / D.PSSE_DIR) == MASTER_SHA256
    )  # the whole set: no new file escapes
    assert _psse_entries(run) == ["psse"]  # no staging left behind


def test_every_island_hour_carries_the_plant_the_scripts_and_the_citation(tmp_path):
    from gridspine.handoff.psse_pack import write_psse_pack
    from tests.gridspine.test_psse_pack import _hour_campus

    run, ms = _run(tmp_path), _model_set()
    bundles = D.psse_campus(run, plant=PLANT, model_set=ms, variant="detailed")
    assert [b.path.name for b in bundles] == ["TEST_HUB_p2030_h0", "TEST_HUB_p2030_h2"]
    hc, case, lines, grid = _hour_campus(tmp_path / "direct")
    direct = write_psse_pack(
        tmp_path / "pack",
        hc,
        case,
        lines,
        grid,
        plant=PLANT,
        model_set=ms,
        variant="detailed",
    )
    assert (
        direct["model_set"]["variant"] == "detailed"
        and direct["model_set"]["model_set_id"] == ms.model_set_id
    )
    for b in bundles:
        assert b.path.parent == run / D.PSSE_DIR and b.path.is_dir()
        pack = b.path / SUBDIR
        m = json.loads((pack / "manifest.json").read_text())
        dyr = (pack / f"{b.path.name}.dyr").read_text().splitlines()
        # PV1 on MV1 (bus 2), one REPCA1 measuring TR1 (bus 2 -> PCC, bus 1)
        assert [ln for ln in dyr if "'REPCA1'" in ln] == [
            "     2 'REPCA1' '2 '     1     2     1  '1 '     0     1     1"
        ]
        assert sorted(p.name for p in pack.glob("run_*.py")) == SCRIPTS
        assert sorted(f for f in m["files"] if f.startswith("run_")) == SCRIPTS
        assert m["model_set"] == direct["model_set"]
        assert (
            m["plant_controller_not_written"] is None
            and m["scripts_not_written"] is None
        )


def test_a_model_set_of_another_campus_is_refused_before_anything_is_written(tmp_path):
    other = campus_spec()
    other["campus"]["name"] = "OTHER_HUB"
    run, ms = _run(tmp_path), _model_set(other)
    with pytest.raises(
        ContractError,
        match=rf"psse: model set {ms.model_set_id} detailed does not describe "
        r"this run's campus \(model hash [0-9a-f]{12}, the model set's",
    ):
        D.psse_campus(run, plant=PLANT, model_set=ms, variant="detailed")
    assert _psse_entries(run) == []


def test_the_check_reads_the_variant_given_not_another(tmp_path):
    """The run's campus is the model set's detailed variant only: citing it as the aggregated one is refused."""
    run, s = _run(tmp_path), campus_spec()
    ms = _model_set_with_aggregated(s)
    D.psse_campus(run, model_set=ms, variant="detailed")
    with pytest.raises(
        ContractError,
        match=rf"{ms.model_set_id} aggregated does not describe this run's campus",
    ):
        D.psse_campus(_run(tmp_path / "b"), model_set=ms, variant="aggregated")
    assert _psse_entries(tmp_path / "b" / "run") == []


def _model_set_with_aggregated(s):
    import datetime

    agg = campus_spec()
    agg["campus"]["name"] = "TEST_HUB_EQ"
    return new_model_set(
        s,
        {"study": "GC-04d"},
        aggregated=agg,
        now=datetime.datetime(2026, 11, 24, tzinfo=datetime.UTC),
    )


def test_a_variant_not_frozen_is_refused_naming_the_model_set(tmp_path):
    run, ms = _run(tmp_path), _model_set()
    with pytest.raises(
        ContractError,
        match=rf"model set {ms.model_set_id}: the aggregated variant was not frozen",
    ):
        D.psse_campus(run, model_set=ms, variant="aggregated")
    with pytest.raises(
        ContractError, match=rf"model set {ms.model_set_id}: unknown variant 'both'"
    ):
        D.psse_campus(run, model_set=ms, variant="both")
    assert _psse_entries(run) == []


@pytest.mark.parametrize("campus_yaml", [None, "campus: [unclosed\n", b"\xff\xfe\x00"])
def test_a_run_whose_campus_cannot_be_read_is_refused_naming_the_model_set(
    tmp_path, campus_yaml
):
    run, ms = _run(tmp_path), _model_set()
    path = run / D.CAMPUS_YAML
    if campus_yaml is None:
        path.unlink()
    elif isinstance(campus_yaml, bytes):
        path.write_bytes(campus_yaml)
    else:
        path.write_text(campus_yaml)
    with pytest.raises(
        ContractError,
        match=rf"psse: model set {ms.model_set_id} detailed: cannot read this "
        r"run's campus",
    ):
        D.psse_campus(run, model_set=ms, variant="detailed")
    assert _psse_entries(run) == []


def test_a_model_set_without_its_variant_is_refused_by_the_pack_leaving_no_tree(
    tmp_path,
):
    run = _run(tmp_path)
    with pytest.raises(
        ContractError, match="give the GC-01 model_set and its variant together"
    ):
        D.psse_campus(run, model_set=_model_set())
    with pytest.raises(
        ContractError, match="give the GC-01 model_set and its variant together"
    ):
        D.psse_campus(run, variant="detailed")
    assert _psse_entries(run) == []


def test_a_plant_that_is_not_a_converter_is_refused_by_the_pack_leaving_no_tree(
    tmp_path,
):
    """psse_pack's own rule, met after the first bundle's files: the staging tree goes with it (GK A2 Q3)."""
    run = _run(tmp_path)
    with pytest.raises(
        ContractError, match="plant unit 'GEN1' is not a grid-following converter"
    ):
        D.psse_campus(run, plant=Plant("GEN1", PocBranch("trafo", 0, 0)))
    assert _psse_entries(run) == []


def test_a_refusal_keeps_the_earlier_tree_and_a_new_call_replaces_it_whole(tmp_path):
    run = _run(tmp_path)
    D.psse_campus(run, hours=[(2030, 1)])
    before = _digests(run / D.PSSE_DIR)
    with pytest.raises(ContractError, match="no hour 7"):
        D.psse_campus(
            run, hours=[(2030, 0), (2030, 7)]
        )  # hour 0 is written before hour 7 is refused
    assert _digests(run / D.PSSE_DIR) == before
    assert _psse_entries(run) == ["psse"]
    D.psse_campus(run)
    assert (
        _digests(run / D.PSSE_DIR) == MASTER_SHA256
    )  # hour 1's bundle is gone, not mixed in


def test_the_check_hashes_the_campus_yaml_as_stored(tmp_path):
    """The hash is the run's stored spec, read back: a run written from the same campus matches."""
    run, ms = _run(tmp_path), _model_set()
    assert yaml.safe_load((run / D.CAMPUS_YAML).read_text()) == campus_spec()
    assert D._cited_spec(run, ms, "detailed") == campus_spec()


def test_a_run_of_the_aggregated_campus_cites_the_aggregated_variant(tmp_path):
    """The variant given reaches every pack: a run whose campus is the aggregated spec cites that variant."""
    s = campus_spec()
    ms = _model_set_with_aggregated(s)
    agg = ms.spec("aggregated")
    run = write_run(tmp_path / "run", agg, selected=THREE)
    bundles = D.psse_campus(run, model_set=ms, variant="aggregated")
    assert [b.path.name for b in bundles] == [
        "TEST_HUB_EQ_p2030_h0",
        "TEST_HUB_EQ_p2030_h2",
    ]
    for b in bundles:
        m = json.loads((b.path / SUBDIR / "manifest.json").read_text())
        assert m["model_set"] == {
            "model_set_id": ms.model_set_id,
            "data_hash": ms.data_hash,
            "variant": "aggregated",
            "model_hash": ms.model_hashes["aggregated"],
        }
        assert ms.model_hashes["aggregated"] != ms.model_hashes["detailed"]
    with pytest.raises(
        ContractError,
        match=rf"{ms.model_set_id} detailed does not describe this run's campus",
    ):
        D.psse_campus(run, model_set=ms, variant="detailed")


def test_no_hours_write_no_tree_and_leave_an_earlier_one_alone(tmp_path):
    """As on master (GK A2 K4): an empty hour list writes nothing and replaces nothing."""
    run = _run(tmp_path)
    assert D.psse_campus(run, hours=[]) == []
    assert _psse_entries(run) == []
    D.psse_campus(run)
    assert D.psse_campus(run, hours=[]) == []
    assert _digests(run / D.PSSE_DIR) == MASTER_SHA256 and _psse_entries(run) == [
        "psse"
    ]


def test_a_stale_stage_of_a_killed_call_is_neither_read_nor_cleared(tmp_path):
    """
    Each call stages in its own fresh ``.psse.partial-<hex>`` (GK A2 K3): a stage a killed process left is
    never written into or read, and is not cleared either, since it may be a running call's.
    """
    run = _run(tmp_path)
    for stale in (".psse.partial", ".psse.partial-dead", ".psse.old-dead"):
        (run / stale / "TEST_HUB_p2030_h0").mkdir(parents=True)
        (run / stale / "TEST_HUB_p2030_h0" / "junk.txt").write_text("stale")
    D.psse_campus(run)
    assert _digests(run / D.PSSE_DIR) == MASTER_SHA256
    assert _psse_entries(run) == [
        ".psse.old-dead",
        ".psse.partial",
        ".psse.partial-dead",
        "psse",
    ]
    assert (
        run / ".psse.partial-dead" / "TEST_HUB_p2030_h0" / "junk.txt"
    ).read_text() == "stale"


def test_a_call_interleaved_with_another_keeps_its_own_hours(tmp_path, monkeypatch):
    """
    Call B runs whole while call A is between its hours (GK A2 K1): B's tree is whole when B returns, A's
    stage is untouched by B, and A's tree replaces B's when A returns (last writer wins).
    """
    from gridspine.handoff import campus_bundle as CB

    run, real, seen = _run(tmp_path), CB.export_campus_bundle, {}

    def export(outdir, *a, **kw):
        b = real(outdir, *a, **kw)
        if not seen:
            seen["started"] = True  # B's own exports pass straight through
            seen["b"] = D.psse_campus(run, hours=[(2030, 1)])
            seen["b_tree"] = _digests(run / D.PSSE_DIR)
            assert all(x.path.is_dir() for x in seen["b"])
        return b

    monkeypatch.setattr(CB, "export_campus_bundle", export)
    a = D.psse_campus(run)
    assert sorted(seen["b_tree"]) and all(
        k.startswith("TEST_HUB_p2030_h1/") for k in seen["b_tree"]
    )
    assert all(x.path.is_dir() for x in a)
    assert _digests(run / D.PSSE_DIR) == MASTER_SHA256 and _psse_entries(run) == [
        "psse"
    ]


def test_the_old_tree_is_put_aside_before_the_new_one_lands(tmp_path, monkeypatch):
    """A delete of the old tree that fails leaves the new tree whole and the old one hidden aside (GK A2 K2)."""
    run = _run(tmp_path)
    D.psse_campus(run, hours=[(2030, 1)])
    real = D.shutil.rmtree

    def rmtree(path, *a, **kw):
        if Path(path).name.startswith(".psse.old-"):
            raise OSError("cannot delete")
        return real(path, *a, **kw)

    monkeypatch.setattr(D.shutil, "rmtree", rmtree)
    D.psse_campus(run)
    assert _digests(run / D.PSSE_DIR) == MASTER_SHA256
    (old,) = [p for p in run.iterdir() if p.name.startswith(".psse.old-")]
    assert sorted(p.name for p in old.iterdir()) == ["TEST_HUB_p2030_h1"]
    assert [p for p in _psse_entries(run) if p.startswith(".psse.partial")] == []


def test_a_failed_swap_puts_the_old_tree_back(tmp_path, monkeypatch):
    """When the stage cannot be renamed in, the earlier tree is restored and the error is raised."""
    run = _run(tmp_path)
    D.psse_campus(run, hours=[(2030, 1)])
    before, real = _digests(run / D.PSSE_DIR), Path.rename

    def rename(self, target):
        if self.name.startswith(".psse.partial-"):
            raise PermissionError("rename refused")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(PermissionError, match="rename refused"):
        D.psse_campus(run)
    assert _digests(run / D.PSSE_DIR) == before and _psse_entries(run) == ["psse"]


def test_a_tree_landing_between_the_renames_is_replaced_not_mixed(
    tmp_path, monkeypatch
):
    """Another call's tree landing between this call's two renames is put aside in turn: last writer wins."""
    run = _run(tmp_path)
    D.psse_campus(run, hours=[(2030, 1)])
    real, landed = Path.rename, []

    def rename(self, target):
        if self.name.startswith(".psse.partial-") and not landed:
            landed.append(True)
            (run / D.PSSE_DIR / "OTHER").mkdir(parents=True)
            (run / D.PSSE_DIR / "OTHER" / "x.txt").write_text("other call")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    D.psse_campus(run)
    assert (
        landed
        and _digests(run / D.PSSE_DIR) == MASTER_SHA256
        and _psse_entries(run) == ["psse"]
    )


def test_a_swap_that_never_settles_is_refused_leaving_no_stage(tmp_path, monkeypatch):
    run = _run(tmp_path)
    real = Path.rename

    def rename(self, target):
        if self.name.startswith(".psse.partial-"):
            (run / D.PSSE_DIR / "OTHER").mkdir(parents=True, exist_ok=True)
            (run / D.PSSE_DIR / "OTHER" / "x.txt").write_text("other call")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(
        ContractError,
        match=rf"kept changing under concurrent calls; tried {D.SWAP_TRIES} times",
    ):
        D.psse_campus(run)
    assert [p for p in _psse_entries(run) if p.startswith(".psse.partial")] == []
