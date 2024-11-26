import shutil
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Mapping, Sequence

from pydantic import SkipValidation

from dagster._components import Component, ComponentLoadContext
from dagster._core.definitions.asset_dep import AssetDep
from dagster._core.definitions.asset_spec import AssetSpec
from dagster._core.definitions.assets import AssetsDefinition
from dagster._core.definitions.decorators.asset_decorator import multi_asset
from dagster._core.definitions.definitions_class import Definitions
from dagster._core.execution.context.asset_execution_context import AssetExecutionContext
from dagster._core.pipes.subprocess import PipesSubprocessClient


# really annoying but necessary (for now) because AssetDep isn't in scope when loading AssetSpec
def _() -> AssetDep: ...


class PythonScriptCollectionComponent(Component):
    name: ClassVar[str] = "python_script_collection"
    resources: Mapping[Literal["pipes_client"], PipesSubprocessClient]

    # TODO: find a way to get rid of SkipValidation
    script_specs: Annotated[Mapping[str, Sequence[AssetSpec]], SkipValidation] = {}

    def generate_files(self) -> None:
        pass

    def get_assets_def(self, path: Path) -> AssetsDefinition:
        # TODO: potentially read the script to get additional configuration
        specs = self.script_specs.get(path.stem, [AssetSpec(key=path.stem)])

        @multi_asset(specs=specs, name=f"script_{path.stem}")
        def _asset(context: AssetExecutionContext, pipes_client: PipesSubprocessClient):
            cmd = [shutil.which("python"), path]
            return pipes_client.run(command=cmd, context=context).get_results()

        return _asset

    def build_defs(self, context: ComponentLoadContext) -> Definitions:
        return Definitions(
            assets=[self.get_assets_def(path) for path in context.path.rglob("*.py")],
            # also really annoying that Literal[<str>] isn't considered a str
            resources=self.resources,  # type: ignore
        )
