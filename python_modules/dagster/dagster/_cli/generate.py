import os
import sys

import click

from dagster._components import (
    CodeLocationProjectContext,
    DeploymentProjectContext,
    is_inside_code_location_project,
    is_inside_deployment_project,
)
from dagster._generate.generate import (
    generate_code_location,
    generate_component_instance,
    generate_component_type,
    generate_deployment,
)


@click.group(name="generate")
def generate_cli():
    """Commands for generating Dagster components and related entities."""


@click.command(name="deployment")
@click.argument("path", type=str)
def generate_deployment_command(path: str) -> None:
    """Generate a Dagster deployment instance."""
    dir_abspath = os.path.abspath(path)
    if os.path.exists(dir_abspath) and os.path.exists(dir_abspath):
        click.echo(
            click.style(f"A file or directory at {dir_abspath} already exists. ", fg="red")
            + "\nPlease delete the contents of this path or choose another location."
        )
        sys.exit(1)
    generate_deployment(path)


@click.command(name="code-location")
@click.argument("name", type=str)
def generate_code_location_command(name: str) -> None:
    """Generate a Dagster code location inside a component."""
    if not is_inside_deployment_project():
        click.echo(
            click.style("This command must be run inside a Dagster deployment project.", fg="red")
        )
        sys.exit(1)

    context = DeploymentProjectContext()
    if context.has_code_location(name):
        click.echo(click.style(f"A code location named {name} already exists.", fg="red"))
        sys.exit(1)

    code_location_path = os.path.join(context.code_location_root, name)
    generate_code_location(code_location_path)


@click.command(name="component-type")
@click.argument("name", type=str)
def generate_component_type_command(name: str) -> None:
    """Generate a Dagster component instance."""
    if not is_inside_code_location_project():
        click.echo(
            click.style(
                "This command must be run inside a Dagster code location project.", fg="red"
            )
        )
        sys.exit(1)

    context = CodeLocationProjectContext()
    if context.has_component_type(name):
        click.echo(click.style(f"A component type named `{name}` already exists.", fg="red"))
        sys.exit(1)

    generate_component_type(context.custom_component_types_root_path, name)


@click.command(name="component-instance")
@click.argument("component-type", type=str)
@click.argument("name", type=str)
def generate_component_instance_command(component_type: str, name: str) -> None:
    """Generate a Dagster component instance."""
    if not is_inside_code_location_project():
        click.echo(
            click.style(
                "This command must be run inside a Dagster code location project.", fg="red"
            )
        )
        sys.exit(1)

    context = CodeLocationProjectContext()
    if not context.has_component_type(component_type):
        click.echo(
            click.style(f"No component type `{component_type}` could be resolved.", fg="red")
        )
        sys.exit(1)
    elif context.has_component_instance(name):
        click.echo(click.style(f"A component instance named `{name}` already exists.", fg="red"))
        sys.exit(1)

    component_type_cls = context.get_component_type(component_type)
    generate_component_instance(context.component_instances_root_path, name, component_type_cls)
