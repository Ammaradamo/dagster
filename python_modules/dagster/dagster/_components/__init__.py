import importlib.util
import os
import sys
from abc import abstractmethod
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, ClassVar, Dict, Final, Iterable, Optional, Type, Union

from dagster._core.errors import DagsterError
from dagster._model import DagsterModel
from dagster._utils import to_snake_case

if TYPE_CHECKING:
    from dagster._core.definitions.definitions_class import Definitions


class Component(DagsterModel):
    name: ClassVar[Optional[str]] = None

    @classmethod
    def registered_name(cls):
        return cls.name or to_snake_case(cls.__name__)

    @classmethod
    def generate_files(cls) -> None:
        raise NotImplementedError()

    @abstractmethod
    def build_defs(self, context: "ComponentLoadContext") -> "Definitions": ...


def is_inside_deployment_project(path: str = ".") -> bool:
    try:
        _resolve_deployment_root(path)
        return True
    except DagsterError:
        return False


def _resolve_deployment_root(path: str) -> str:
    current_path = os.path.abspath(path)
    while not _is_deployment_root(current_path):
        current_path = os.path.dirname(current_path)
        if current_path == "/":
            raise DagsterError("Cannot find deployment root")
    return current_path


def is_inside_code_location_project(path: str = ".") -> bool:
    try:
        _resolve_code_location_root(path)
        return True
    except DagsterError:
        return False


def _resolve_code_location_root(path: str) -> str:
    current_path = os.path.abspath(path)
    while not _is_code_location_root(current_path):
        current_path = os.path.dirname(current_path)
        if current_path == "/":
            raise DagsterError("Cannot find code location root")
    return current_path


def _is_deployment_root(path: str) -> bool:
    return os.path.exists(os.path.join(path, "code_locations"))


def _is_code_location_root(path: str) -> bool:
    return os.path.basename(os.path.dirname(path)) == "code_locations"


# Deployment
_DEPLOYMENT_CODE_LOCATIONS_DIR: Final = "code_locations"

# Code location
_CODE_LOCATION_CUSTOM_COMPONENTS_DIR: Final = "lib"
_CODE_LOCATION_COMPONENT_INSTANCES_DIR: Final = "components"


class DeploymentProjectContext:
    def __init__(self, path: Optional[Union[str, Path]] = None):
        path = Path(path or os.getcwd())
        self._deployment_root = _resolve_deployment_root(str(path))

    @property
    def deployment_root(self) -> str:
        return os.path.join(self._deployment_root, _DEPLOYMENT_CODE_LOCATIONS_DIR)

    @property
    def code_location_root(self) -> str:
        return os.path.join(self._deployment_root, _DEPLOYMENT_CODE_LOCATIONS_DIR)

    def has_code_location(self, name: str) -> bool:
        return os.path.exists(os.path.join(self._deployment_root, "code_locations", name))


class CodeLocationProjectContext:
    def __init__(self, path: Optional[Union[str, Path]] = None):
        path = Path(path or os.getcwd())
        self._deployment_context = DeploymentProjectContext(path)
        self._root = _resolve_code_location_root(str(path))
        self._name = os.path.basename(self._root)
        self._component_registry = ComponentRegistry()

        # Make sure we can import from the cwd
        if sys.path[0] != "":
            sys.path.insert(0, "")

        module = importlib.import_module(self.custom_component_types_root_module)
        register_components_in_module(self._component_registry, module)

    @property
    def deployment_context(self) -> DeploymentProjectContext:
        return self._deployment_context

    @property
    def custom_component_types_root_path(self) -> str:
        return os.path.join(self._root, self._name, _CODE_LOCATION_CUSTOM_COMPONENTS_DIR)

    @property
    def custom_component_types_root_module(self) -> str:
        return f"{self._name}.{_CODE_LOCATION_CUSTOM_COMPONENTS_DIR}"

    def has_component_type(self, name: str) -> bool:
        return self._component_registry.has(name)

    def get_component_type(self, name: str) -> Type[Component]:
        if not self.has_component_type(name):
            raise DagsterError(f"No component type named {name}")
        return self._component_registry.get(name)

    @property
    def component_instances_root_path(self) -> str:
        return os.path.join(self._root, self._name, _CODE_LOCATION_COMPONENT_INSTANCES_DIR)

    @property
    def component_instances(self) -> Iterable[str]:
        return os.listdir(
            os.path.join(self._root, self._name, _CODE_LOCATION_COMPONENT_INSTANCES_DIR)
        )

    def has_component_instance(self, name: str) -> bool:
        return os.path.exists(
            os.path.join(self._root, self._name, _CODE_LOCATION_COMPONENT_INSTANCES_DIR, name)
        )


class ComponentLoadContext:
    def __init__(self, path: Path):
        # TODO: hook this up to the component registry
        self.path = path

    def load_component(self, component: Component) -> "Definitions":
        # TODO: ability to load a component from yaml
        # if component is None:
        #    component_config_path = path / "defs.yml"
        #    component_config = yaml_safe_load(component_config_path)
        #    component_name = component_config["name"]
        #    component_class = self.get_component_type(component_name)
        #    component = load_pydantic_model_from_path(component_class, path)
        return component.build_defs(self)


def load_component_from_path(path: Path, component: Component) -> "Definitions":
    return ComponentLoadContext(path).load_component(component)


class ComponentRegistry:
    def __init__(self):
        self._components: Dict[str, Type[Component]] = {}

    def register(self, name: str, component: Type[Component]) -> None:
        self._components[name] = component

    def has(self, name: str) -> bool:
        return name in self._components

    def get(self, name: str) -> Type[Component]:
        return self._components[name]

    def keys(self) -> Iterable[str]:
        return self._components.keys()

    def __repr__(self):
        return f"<ComponentRegistry {list(self._components.keys())}>"


def register_components_in_module(registry: ComponentRegistry, root_module: ModuleType) -> None:
    from dagster._core.definitions.load_assets_from_modules import (
        find_modules_in_package,
        find_objects_in_module_of_types,
    )

    for module in find_modules_in_package(root_module):
        for component in find_objects_in_module_of_types(module, (Component,), mode="subclass"):
            if component is Component:
                continue
            name = f"{module.__name__}[{component.registered_name()}]"
            registry.register(name, component)
