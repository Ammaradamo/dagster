import asyncio
from typing import TYPE_CHECKING, Dict, List, Optional

import graphene
from dagster import _check as check
from dagster._core.definitions.asset_graph_differ import AssetGraphDiffer
from dagster._core.definitions.partition import CachingDynamicPartitionsLoader
from dagster._core.definitions.sensor_definition import SensorType
from dagster._core.remote_representation import (
    CodeLocation,
    GrpcServerCodeLocation,
    ManagedGrpcPythonEnvCodeLocationOrigin,
    RemoteRepository,
)
from dagster._core.remote_representation.feature_flags import get_feature_flags_for_location
from dagster._core.remote_representation.grpc_server_state_subscriber import (
    LocationStateChangeEvent,
    LocationStateChangeEventType,
    LocationStateSubscriber,
)
from dagster._core.remote_representation.handle import RepositoryHandle
from dagster._core.workspace.context import WorkspaceProcessContext
from dagster._core.workspace.workspace import CodeLocationEntry, CodeLocationLoadStatus

from dagster_graphql.implementation.asset_checks_loader import AssetChecksLoader
from dagster_graphql.implementation.fetch_solids import get_solid, get_solids
from dagster_graphql.implementation.loader import RepositoryScopedBatchLoader, StaleStatusLoader
from dagster_graphql.schema.asset_graph import GrapheneAssetGroup, GrapheneAssetNode
from dagster_graphql.schema.errors import GraphenePythonError, GrapheneRepositoryNotFoundError
from dagster_graphql.schema.partition_sets import GraphenePartitionSet
from dagster_graphql.schema.permissions import GraphenePermission
from dagster_graphql.schema.pipelines.pipeline import GrapheneJob, GraphenePipeline
from dagster_graphql.schema.repository_origin import (
    GrapheneRepositoryMetadata,
    GrapheneRepositoryOrigin,
)
from dagster_graphql.schema.resources import GrapheneResourceDetails
from dagster_graphql.schema.schedules import GrapheneSchedule
from dagster_graphql.schema.sensors import GrapheneSensor, GrapheneSensorType
from dagster_graphql.schema.used_solid import GrapheneUsedSolid
from dagster_graphql.schema.util import ResolveInfo, non_null_list

if TYPE_CHECKING:
    from dagster._core.remote_representation.external_data import AssetNodeSnap

GrapheneLocationStateChangeEventType = graphene.Enum.from_enum(LocationStateChangeEventType)


class GrapheneDagsterLibraryVersion(graphene.ObjectType):
    name = graphene.NonNull(graphene.String)
    version = graphene.NonNull(graphene.String)

    class Meta:
        name = "DagsterLibraryVersion"


class GrapheneRepositoryLocationLoadStatus(graphene.Enum):
    LOADING = "LOADING"
    LOADED = "LOADED"

    class Meta:
        name = "RepositoryLocationLoadStatus"

    @classmethod
    def from_python_status(cls, python_status):
        check.inst_param(python_status, "python_status", CodeLocationLoadStatus)
        if python_status == CodeLocationLoadStatus.LOADING:
            return GrapheneRepositoryLocationLoadStatus.LOADING
        elif python_status == CodeLocationLoadStatus.LOADED:
            return GrapheneRepositoryLocationLoadStatus.LOADED
        else:
            check.failed(f"Invalid location load status: {python_status}")


class GrapheneRepositoryLocation(graphene.ObjectType):
    id = graphene.NonNull(graphene.ID)
    name = graphene.NonNull(graphene.String)
    is_reload_supported = graphene.NonNull(graphene.Boolean)
    environment_path = graphene.String()
    repositories = non_null_list(lambda: GrapheneRepository)
    server_id = graphene.String()
    dagsterLibraryVersions = graphene.List(graphene.NonNull(GrapheneDagsterLibraryVersion))

    class Meta:
        name = "RepositoryLocation"

    def __init__(self, location: CodeLocation):
        self._location = check.inst_param(location, "location", CodeLocation)
        environment_path = (
            location.origin.loadable_target_origin.executable_path
            if isinstance(location.origin, ManagedGrpcPythonEnvCodeLocationOrigin)
            else None
        )

        server_id = location.server_id if isinstance(location, GrpcServerCodeLocation) else None

        check.invariant(location.name is not None)

        super().__init__(
            name=location.name,
            environment_path=environment_path,
            is_reload_supported=location.is_reload_supported,
            server_id=server_id,
        )

    def resolve_id(self, _) -> str:
        return self.name

    def resolve_repositories(self, graphene_info: ResolveInfo):
        return [
            GrapheneRepository(repository.handle)
            for repository in self._location.get_repositories().values()
        ]

    def resolve_dagsterLibraryVersions(self, _: ResolveInfo):
        libs = self._location.get_dagster_library_versions()
        if libs is None:
            return None

        return [GrapheneDagsterLibraryVersion(name, ver) for name, ver in libs.items()]


class GrapheneRepositoryLocationOrLoadError(graphene.Union):
    class Meta:
        types = (
            GrapheneRepositoryLocation,
            GraphenePythonError,
        )
        name = "RepositoryLocationOrLoadError"


class GrapheneWorkspaceLocationStatusEntry(graphene.ObjectType):
    id = graphene.NonNull(graphene.ID)
    name = graphene.NonNull(graphene.String)
    loadStatus = graphene.NonNull(GrapheneRepositoryLocationLoadStatus)
    updateTimestamp = graphene.NonNull(graphene.Float)
    versionKey = graphene.NonNull(graphene.String)

    permissions = graphene.Field(non_null_list(GraphenePermission))

    class Meta:
        name = "WorkspaceLocationStatusEntry"

    def __init__(
        self,
        id,
        name,
        load_status,
        update_timestamp,
        version_key,
    ):
        super().__init__(
            id=id,
            name=name,
            loadStatus=load_status,
            updateTimestamp=update_timestamp,
            versionKey=version_key,
        )

    def resolve_permissions(self, graphene_info):
        permissions = graphene_info.context.permissions_for_location(location_name=self.name)
        return [GraphenePermission(permission, value) for permission, value in permissions.items()]


class GrapheneWorkspaceLocationStatusEntries(graphene.ObjectType):
    entries = non_null_list(GrapheneWorkspaceLocationStatusEntry)

    class Meta:
        name = "WorkspaceLocationStatusEntries"


class GrapheneWorkspaceLocationStatusEntriesOrError(graphene.Union):
    class Meta:
        types = (
            GrapheneWorkspaceLocationStatusEntries,
            GraphenePythonError,
        )
        name = "WorkspaceLocationStatusEntriesOrError"


class GrapheneFeatureFlag(graphene.ObjectType):
    class Meta:
        name = "FeatureFlag"

    name = graphene.NonNull(graphene.String)
    enabled = graphene.NonNull(graphene.Boolean)


class GrapheneWorkspaceLocationEntry(graphene.ObjectType):
    id = graphene.NonNull(graphene.ID)
    name = graphene.NonNull(graphene.String)
    locationOrLoadError = graphene.Field(GrapheneRepositoryLocationOrLoadError)
    loadStatus = graphene.NonNull(GrapheneRepositoryLocationLoadStatus)
    displayMetadata = non_null_list(GrapheneRepositoryMetadata)
    updatedTimestamp = graphene.NonNull(graphene.Float)
    versionKey = graphene.NonNull(graphene.String)

    permissions = graphene.Field(non_null_list(GraphenePermission))

    featureFlags = non_null_list(GrapheneFeatureFlag)

    class Meta:
        name = "WorkspaceLocationEntry"

    def __init__(self, location_entry: CodeLocationEntry):
        self._location_entry = check.inst_param(location_entry, "location_entry", CodeLocationEntry)
        super().__init__(name=self._location_entry.origin.location_name)

    def resolve_id(self, _):
        return self.name

    def resolve_locationOrLoadError(self, _):
        if self._location_entry.code_location:
            return GrapheneRepositoryLocation(self._location_entry.code_location)

        error = self._location_entry.load_error
        return GraphenePythonError(error) if error else None

    def resolve_loadStatus(self, _):
        return GrapheneRepositoryLocationLoadStatus.from_python_status(
            self._location_entry.load_status
        )

    def resolve_displayMetadata(self, _):
        metadata = self._location_entry.display_metadata
        return [
            GrapheneRepositoryMetadata(key=key, value=value)
            for key, value in metadata.items()
            if value is not None
        ]

    def resolve_updatedTimestamp(self, _) -> float:
        return self._location_entry.update_timestamp

    def resolve_versionKey(self, _) -> str:
        return self._location_entry.version_key

    def resolve_permissions(self, graphene_info):
        permissions = graphene_info.context.permissions_for_location(location_name=self.name)
        return [GraphenePermission(permission, value) for permission, value in permissions.items()]

    def resolve_featureFlags(self, graphene_info):
        feature_flags = get_feature_flags_for_location(self._location_entry)
        return [
            GrapheneFeatureFlag(name=feature_flag_name.value, enabled=feature_flag_enabled)
            for feature_flag_name, feature_flag_enabled in feature_flags.items()
        ]


class GrapheneRepository(graphene.ObjectType):
    id = graphene.NonNull(graphene.ID)
    name = graphene.NonNull(graphene.String)
    location = graphene.NonNull(GrapheneRepositoryLocation)
    pipelines = non_null_list(GraphenePipeline)
    jobs = non_null_list(GrapheneJob)
    usedSolids = graphene.Field(non_null_list(GrapheneUsedSolid))
    usedSolid = graphene.Field(GrapheneUsedSolid, name=graphene.NonNull(graphene.String))
    origin = graphene.NonNull(GrapheneRepositoryOrigin)
    partitionSets = non_null_list(GraphenePartitionSet)
    schedules = non_null_list(GrapheneSchedule)
    sensors = graphene.Field(
        non_null_list(GrapheneSensor), sensorType=graphene.Argument(GrapheneSensorType)
    )
    assetNodes = non_null_list(GrapheneAssetNode)
    displayMetadata = non_null_list(GrapheneRepositoryMetadata)
    assetGroups = non_null_list(GrapheneAssetGroup)
    allTopLevelResourceDetails = non_null_list(GrapheneResourceDetails)

    class Meta:
        name = "Repository"

    def __init__(
        self,
        handle: RepositoryHandle,
    ):
        # Warning! GrapheneAssetNode contains a GrapheneRepository. Any computation in this
        # __init__ will be done **once per asset**. Ensure that any expensive work is done
        # elsewhere or cached.
        self._handle = handle

        self._batch_loader = None

        super().__init__(name=handle.repository_name)

    def get_repository(self, graphene_info: ResolveInfo) -> RemoteRepository:
        return graphene_info.context.get_repository(self._handle.to_selector())

    def get_batch_loader(self, graphene_info: ResolveInfo):
        if self._batch_loader is None:
            self._batch_loader = RepositoryScopedBatchLoader(
                graphene_info.context.instance, self.get_repository(graphene_info)
            )
        return self._batch_loader

    def resolve_id(self, _graphene_info: ResolveInfo) -> str:
        return self._handle.get_compound_id().to_string()

    def resolve_origin(self, _graphene_info: ResolveInfo):
        origin = self._handle.get_remote_origin()
        return GrapheneRepositoryOrigin(origin)

    def resolve_location(self, graphene_info: ResolveInfo):
        return GrapheneRepositoryLocation(
            graphene_info.context.get_code_location(self._handle.location_name)
        )

    def resolve_schedules(self, graphene_info: ResolveInfo):
        batch_loader = self.get_batch_loader(graphene_info)
        repository = self.get_repository(graphene_info)
        return sorted(
            [
                GrapheneSchedule(
                    schedule,
                    repository,
                    batch_loader.get_schedule_state(schedule.name),
                    batch_loader,
                )
                for schedule in repository.get_external_schedules()
            ],
            key=lambda schedule: schedule.name,
        )

    def resolve_sensors(self, graphene_info: ResolveInfo, sensorType: Optional[SensorType] = None):
        batch_loader = self.get_batch_loader(graphene_info)
        repository = self.get_repository(graphene_info)
        return [
            GrapheneSensor(
                sensor,
                repository,
                batch_loader.get_sensor_state(sensor.name),
                batch_loader,
            )
            for sensor in sorted(repository.get_external_sensors(), key=lambda sensor: sensor.name)
            if not sensorType or sensor.sensor_type == sensorType
        ]

    def resolve_pipelines(self, graphene_info: ResolveInfo):
        return [
            GraphenePipeline(pipeline)
            for pipeline in sorted(
                self.get_repository(graphene_info).get_all_external_jobs(),
                key=lambda pipeline: pipeline.name,
            )
        ]

    def resolve_jobs(self, graphene_info: ResolveInfo):
        return [
            GrapheneJob(pipeline)
            for pipeline in sorted(
                self.get_repository(graphene_info).get_all_external_jobs(),
                key=lambda pipeline: pipeline.name,
            )
        ]

    def resolve_usedSolid(self, graphene_info: ResolveInfo, name):
        return get_solid(self.get_repository(graphene_info), name)

    def resolve_usedSolids(self, graphene_info: ResolveInfo):
        return get_solids(self.get_repository(graphene_info))

    def resolve_partitionSets(self, graphene_info: ResolveInfo):
        return (
            GraphenePartitionSet(self._handle, partition_set)
            for partition_set in self.get_repository(graphene_info).get_external_partition_sets()
        )

    def resolve_displayMetadata(self, graphene_info: ResolveInfo):
        metadata = self._handle.display_metadata
        return [
            GrapheneRepositoryMetadata(key=key, value=value)
            for key, value in metadata.items()
            if value is not None
        ]

    def resolve_assetNodes(self, graphene_info: ResolveInfo):
        asset_node_snaps = self.get_repository(graphene_info).get_asset_node_snaps()
        asset_checks_loader = AssetChecksLoader(
            context=graphene_info.context,
            asset_keys=[node.asset_key for node in asset_node_snaps],
        )

        asset_graph_differ = None
        base_deployment_context = graphene_info.context.get_base_deployment_context()
        if base_deployment_context is not None:
            # then we are in a branch deployment
            asset_graph_differ = AssetGraphDiffer.from_external_repositories(
                code_location_name=self._handle.location_name,
                repository_name=self._handle.repository_name,
                branch_workspace=graphene_info.context,
                base_workspace=base_deployment_context,
            )

        dynamic_partitions_loader = CachingDynamicPartitionsLoader(
            graphene_info.context.instance,
        )
        stale_status_loader = StaleStatusLoader(
            instance=graphene_info.context.instance,
            asset_graph=lambda: self.get_repository(graphene_info).asset_graph,
            loading_context=graphene_info.context,
        )

        return [
            GrapheneAssetNode(
                repository_handle=self._handle,
                asset_node_snap=asset_node_snap,
                asset_checks_loader=asset_checks_loader,
                stale_status_loader=stale_status_loader,
                dynamic_partitions_loader=dynamic_partitions_loader,
                asset_graph_differ=asset_graph_differ,
            )
            for asset_node_snap in self.get_repository(graphene_info).get_asset_node_snaps()
        ]

    def resolve_assetGroups(self, graphene_info: ResolveInfo):
        groups: Dict[str, List[AssetNodeSnap]] = {}
        for asset_node_snap in self.get_repository(graphene_info).get_asset_node_snaps():
            if not asset_node_snap.group_name:
                continue
            external_assets = groups.setdefault(asset_node_snap.group_name, [])
            external_assets.append(asset_node_snap)

        return [
            GrapheneAssetGroup(
                f"{self._handle.location_name}-{self._handle.repository_name}-{group_name}",
                group_name,
                [external_node.asset_key for external_node in external_nodes],
            )
            for group_name, external_nodes in groups.items()
        ]

    def resolve_allTopLevelResourceDetails(self, graphene_info) -> List[GrapheneResourceDetails]:
        return [
            GrapheneResourceDetails(
                location_name=self._handle.location_name,
                repository_name=self._handle.repository_name,
                external_resource=resource,
            )
            for resource in sorted(
                self.get_repository(graphene_info).get_external_resources(),
                key=lambda resource: resource.name,
            )
            if resource.is_top_level
        ]


class GrapheneRepositoryConnection(graphene.ObjectType):
    nodes = non_null_list(GrapheneRepository)

    class Meta:
        name = "RepositoryConnection"


class GrapheneWorkspace(graphene.ObjectType):
    id = graphene.NonNull(graphene.String)
    locationEntries = non_null_list(GrapheneWorkspaceLocationEntry)

    class Meta:
        name = "Workspace"

    def resolve_id(self, _graphene_info: ResolveInfo):
        return "Workspace"


class GrapheneLocationStateChangeEvent(graphene.ObjectType):
    event_type = graphene.NonNull(GrapheneLocationStateChangeEventType)
    message = graphene.NonNull(graphene.String)
    location_name = graphene.NonNull(graphene.String)
    server_id = graphene.Field(graphene.String)

    class Meta:
        name = "LocationStateChangeEvent"


class GrapheneLocationStateChangeSubscription(graphene.ObjectType):
    event = graphene.Field(graphene.NonNull(GrapheneLocationStateChangeEvent))

    class Meta:
        name = "LocationStateChangeSubscription"


async def gen_location_state_changes(graphene_info: ResolveInfo):
    # This lives on the process context and is never modified/destroyed, so we can
    # access it directly
    context = graphene_info.context.process_context

    if not isinstance(context, WorkspaceProcessContext):
        return

    queue: asyncio.Queue[LocationStateChangeEvent] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _enqueue(event):
        loop.call_soon_threadsafe(queue.put_nowait, event)

    token = context.add_state_subscriber(LocationStateSubscriber(_enqueue))
    try:
        while True:
            event = await queue.get()
            yield GrapheneLocationStateChangeSubscription(
                event=GrapheneLocationStateChangeEvent(
                    event_type=event.event_type,
                    location_name=event.location_name,
                    message=event.message,
                    server_id=event.server_id,
                ),
            )
    finally:
        context.rm_state_subscriber(token)


class GrapheneRepositoriesOrError(graphene.Union):
    class Meta:
        types = (
            GrapheneRepositoryConnection,
            GrapheneRepositoryNotFoundError,
            GraphenePythonError,
        )
        name = "RepositoriesOrError"


class GrapheneWorkspaceOrError(graphene.Union):
    class Meta:
        types = (GrapheneWorkspace, GraphenePythonError)
        name = "WorkspaceOrError"


class GrapheneRepositoryOrError(graphene.Union):
    class Meta:
        types = (
            GraphenePythonError,
            GrapheneRepository,
            GrapheneRepositoryNotFoundError,
        )
        name = "RepositoryOrError"


class GrapheneWorkspaceLocationEntryOrError(graphene.Union):
    class Meta:
        types = (GrapheneWorkspaceLocationEntry, GraphenePythonError)
        name = "WorkspaceLocationEntryOrError"


types = [
    GrapheneLocationStateChangeEvent,
    GrapheneLocationStateChangeEventType,
    GrapheneLocationStateChangeSubscription,
    GrapheneRepositoriesOrError,
    GrapheneRepository,
    GrapheneRepositoryConnection,
    GrapheneRepositoryLocation,
    GrapheneRepositoryOrError,
    GrapheneWorkspaceLocationEntryOrError,
]
