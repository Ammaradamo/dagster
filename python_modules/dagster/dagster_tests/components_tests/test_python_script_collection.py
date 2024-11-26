from pathlib import Path

from dagster._components import load_component_from_path
from dagster._components.impls.python_script_collection import PythonScriptCollectionComponent
from dagster._core.definitions.asset_spec import AssetSpec
from dagster._core.pipes.subprocess import PipesSubprocessClient

LOCATION_PATH = Path(__file__).parent / "code_locations" / "python_script_location"


def test_no_config() -> None:
    defs = load_component_from_path(
        LOCATION_PATH / "scripts",
        PythonScriptCollectionComponent(resources={"pipes_client": PipesSubprocessClient()}),
    )

    assert len(defs.get_asset_graph().get_all_asset_keys()) == 3
    result = defs.get_implicit_global_asset_job_def().execute_in_process()
    assert result.success


def test_script_config() -> None:
    defs = load_component_from_path(
        LOCATION_PATH / "scripts",
        PythonScriptCollectionComponent(
            resources={"pipes_client": PipesSubprocessClient()},
            script_specs={
                "script_one": [AssetSpec(key="one_a"), AssetSpec(key="one_b")],
            },
        ),
    )

    assert len(defs.get_asset_graph().get_all_asset_keys()) == 4
    result = defs.get_implicit_global_asset_job_def().execute_in_process()
    assert result.success
