import pytest
import responses
from dagster import Failure
from dagster._vendored.dateutil import parser
from dagster_fivetran import FivetranWorkspace
from dagster_fivetran.translator import MIN_TIME_STR

from dagster_fivetran_tests.experimental.conftest import (
    FIVETRAN_API_BASE,
    FIVETRAN_API_VERSION,
    FIVETRAN_CONNECTOR_ENDPOINT,
    TEST_ACCOUNT_ID,
    TEST_API_KEY,
    TEST_API_SECRET,
    TEST_MAX_TIME_STR,
    TEST_PREVIOUS_MAX_TIME_STR,
    get_sample_connection_details,
)


def test_basic_resource_request(
    connector_id: str,
    destination_id: str,
    group_id: str,
    all_api_mocks: responses.RequestsMock,
) -> None:
    resource = FivetranWorkspace(
        account_id=TEST_ACCOUNT_ID, api_key=TEST_API_KEY, api_secret=TEST_API_SECRET
    )
    client = resource.get_client()

    # fetch workspace data calls
    client.get_connectors_for_group(group_id=group_id)
    client.get_destination_details(destination_id=destination_id)
    client.get_groups()
    client.get_schema_config_for_connector(connector_id=connector_id)

    assert len(all_api_mocks.calls) == 4
    assert "Basic" in all_api_mocks.calls[0].request.headers["Authorization"]
    assert group_id in all_api_mocks.calls[0].request.url
    assert destination_id in all_api_mocks.calls[1].request.url
    assert "groups" in all_api_mocks.calls[2].request.url
    assert f"{connector_id}/schemas" in all_api_mocks.calls[3].request.url

    # connector details calls
    all_api_mocks.calls.reset()
    client.get_connector_details(connector_id=connector_id)
    client.update_schedule_type_for_connector(connector_id=connector_id, schedule_type="auto")

    assert len(all_api_mocks.calls) == 2
    assert connector_id in all_api_mocks.calls[0].request.url
    assert connector_id in all_api_mocks.calls[1].request.url
    assert all_api_mocks.calls[1].request.method == "PATCH"

    # sync calls
    all_api_mocks.calls.reset()
    client.start_sync(connector_id=connector_id)
    assert len(all_api_mocks.calls) == 3
    assert f"{connector_id}/force" in all_api_mocks.calls[2].request.url

    # resync calls
    all_api_mocks.calls.reset()
    client.start_resync(connector_id=connector_id, resync_parameters=None)
    assert len(all_api_mocks.calls) == 3
    assert f"{connector_id}/resync" in all_api_mocks.calls[2].request.url

    # resync calls with parameters
    all_api_mocks.calls.reset()
    client.start_resync(connector_id=connector_id, resync_parameters={"property1": ["string"]})
    assert len(all_api_mocks.calls) == 3
    assert f"{connector_id}/schemas/tables/resync" in all_api_mocks.calls[2].request.url

    # poll calls
    # Succeeded poll
    all_api_mocks.calls.reset()
    client.poll_sync(
        connector_id=connector_id, previous_sync_completed_at=parser.parse(MIN_TIME_STR)
    )
    assert len(all_api_mocks.calls) == 1

    # Timed out poll
    all_api_mocks.calls.reset()
    with pytest.raises(Failure) as e:
        client.poll_sync(
            connector_id=connector_id,
            # The poll process will time out because the value of
            # `FivetranConnector.last_sync_completed_at` does not change in the test
            previous_sync_completed_at=parser.parse(TEST_MAX_TIME_STR),
            poll_timeout=2,
            poll_interval=1,
        )
    assert f"Sync for connector '{connector_id}' timed out" in str(e.value)

    # Failed poll
    all_api_mocks.calls.reset()
    # Replace the mock API call and set `failed_at` as more recent that `succeeded_at`
    all_api_mocks.replace(
        method_or_response=responses.GET,
        url=f"{FIVETRAN_API_BASE}/{FIVETRAN_API_VERSION}/{FIVETRAN_CONNECTOR_ENDPOINT}/{connector_id}",
        json=get_sample_connection_details(
            succeeded_at=TEST_PREVIOUS_MAX_TIME_STR, failed_at=TEST_MAX_TIME_STR
        ),
        status=200,
    )
    with pytest.raises(Failure) as e:
        client.poll_sync(
            connector_id=connector_id,
            previous_sync_completed_at=parser.parse(MIN_TIME_STR),
            poll_timeout=2,
            poll_interval=1,
        )
    assert f"Sync for connector '{connector_id}' failed!" in str(e.value)
