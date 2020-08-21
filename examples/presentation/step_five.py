"""Testable resources"""
from dagster import repository, file_relative_path, PresetDefinition  # isort:skip


def get_in_repo_preset_definition():
    return PresetDefinition(
        "persisting",
        mode="persisting",
        run_config={
            "solids": {
                "add_sugar_per_cup": {
                    "inputs": {
                        "cereals": {
                            "csv": {"path": file_relative_path(__file__, "data/cereal.csv")}
                        }
                    }
                }
            },
            "execution": {"multiprocess": {}},
            "storage": {"filesystem": {}},
        },
    )


import os
import shutil

from dagster_pandas import DataFrame

from dagster import Field, ModeDefinition, pipeline, resource, solid
from dagster.utils import mkdir_p


@solid
def add_sugar_per_cup(_, cereals: DataFrame):
    df = cereals[["name"]]
    df["sugar_per_cup"] = cereals["sugars"] / cereals["cups"]
    return df


@solid
def compute_cutoff(_, cereals: DataFrame) -> float:
    return cereals["sugar_per_cup"].quantile(0.75)


@solid
def filter_below_cutoff(_, cereals: DataFrame, cutoff: float) -> DataFrame:
    return cereals[cereals["sugar_per_cup"] > cutoff]


class TempPandasMetastore:
    def __init__(self, root_dir):
        self.root_dir = root_dir

    def save(self, key, df):
        df.to_parquet(os.path.join(self.root_dir, key))


class FakePandasMetastore:
    def __init__(self):
        self.dfs = {}

    def save(self, key, df):
        self.dfs[key] = df


@resource
def fake_pandas_metastore(_):
    return FakePandasMetastore()


@resource(
    config_schema={
        "root_dir": Field(str, default_value="/tmp/pandas_metastore", is_required=False),
        "autocreate_root_dir": Field(
            bool,
            default_value=True,
            is_required=False,
            description="Automatically create the root directory if it is not already present",
        ),
        "cleanup": Field(
            bool,
            default_value=False,
            is_required=False,
            description="If True, this will delete any persisted files",
        ),
    }
)
def tempdir_pandas_metastore(init_context):
    root_dir = init_context.resource_config["root_dir"]
    if init_context.resource_config["autocreate_root_dir"]:
        init_context.log.info("About to create {root_dir}".format(root_dir=root_dir))
        mkdir_p(root_dir)
    try:
        yield TempPandasMetastore(root_dir)
    finally:
        if init_context.resource_config["cleanup"]:
            shutil.rmtree(root_dir)


@solid(required_resource_keys={"my_metastore"})
def save_to_my_metastore(context, cereals: DataFrame):
    context.log.info(
        "About to persist df in cereal key. Metastore class: {klass}".format(
            klass=type(context.resources.my_metastore)
        )
    )
    context.resources.my_metastore.save("cereals", cereals)


@pipeline(
    mode_defs=[
        ModeDefinition(name="persisting", resource_defs={"my_metastore": tempdir_pandas_metastore}),
        ModeDefinition(name="faked", resource_defs={"my_metastore": fake_pandas_metastore}),
    ],
    preset_defs=[get_in_repo_preset_definition()],
)
def compute_top_quartile_pipeline():
    with_per_cup = add_sugar_per_cup()
    save_to_my_metastore(
        filter_below_cutoff(cereals=with_per_cup, cutoff=compute_cutoff(with_per_cup))
    )


@repository
def step_five_repo():
    return [compute_top_quartile_pipeline]
