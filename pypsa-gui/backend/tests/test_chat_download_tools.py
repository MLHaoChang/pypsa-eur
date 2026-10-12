"""Download tool results must be real, authenticated browser file links."""
import io
import json

import openpyxl

from services import chat_tools
from tests.test_openai_conversations_live import network


def test_weightings_download_metadata_round_trips_through_real_route(client, install_network):
    net = network(install_network)
    result = chat_tools.download_snapshot_weightings_csv()
    json.dumps(result)
    assert result["filename"] == "snapshot_weightings.csv"
    assert result["media_type"] == "text/csv"
    download = client.get(result["download_url"])
    assert download.status_code == 200
    assert set(download.text.splitlines()[0].split(",")) == {"snapshot", "objective", "generators", "stores"}
    assert len(download.text.strip().splitlines()) == len(net.snapshots) + 1


def test_load_template_metadata_downloads_an_actual_workbook(client, install_network):
    network(install_network)
    result = chat_tools.download_timeseries_template("loads")
    json.dumps(result)
    assert result["filename"].endswith(".xlsx")
    assert result["media_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    download = client.get(result["download_url"])
    assert download.status_code == 200
    book = openpyxl.load_workbook(io.BytesIO(download.content), read_only=True)
    assert "L0" in next(book.active.values)
