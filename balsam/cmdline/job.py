from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Set, Union, cast

import click
import yaml

from balsam.schemas import JobState

from .utils import load_site_config, validate_tags

if TYPE_CHECKING:
    from balsam._api.models import App
    from balsam.client import RESTClient  # noqa: F401


@click.group()
@click.pass_context
def job(ctx: Any) -> None:
    """
    Create and monitor Balsam Jobs
    """
    ctx.obj = load_site_config()


def validate_state(ctx: Any, param: Any, value: Union[None, str, JobState]) -> Union[None, str, JobState]:
    if value is None:
        return value
    if not JobState.is_valid(value):
        raise click.BadParameter(f"Invalid state {value}")
    return value


def validate_app(ctx: Any, param: Any, value: str) -> "App":
    site_id = ctx.obj.settings.site_id
    client = cast("RESTClient", ctx.obj.client)
    App = client.App
    lookup = {"site_id": site_id}
    if value.isdigit():
        lookup["id"] = int(value)
    else:
        lookup["class_path"] = value
    try:
        app = App.objects.get(**lookup)
    except App.DoesNotExist:
        raise click.BadParameter(f"No App matching criteria {lookup}")
    return app


def validate_set(all_params: Set[str], required_params: Set[str], provided_params: Set[str]) -> None:
    missing_params = required_params.difference(provided_params)
    extraneous_params = provided_params.difference(all_params)
    if missing_params:
        raise click.BadParameter(f"Missing required: {missing_params}")
    if extraneous_params:
        raise click.BadParameter(f"Extraneous values: {extraneous_params}")


def validate_parameters(parameters: List[str], app: "App") -> Dict[str, str]:
    params = validate_tags(None, None, parameters)
    all_params = set(app.parameters.keys())
    required_params = {k for k in all_params if app.parameters[k].required}
    provided = set(params.keys())
    validate_set(all_params, required_params, provided)
    return params


def validate_transfers(ctx: Any, param: Any, value: List[str]) -> Dict[str, Dict[str, Any]]:
    transfers = validate_tags(ctx, param, value)
    app = ctx.params["app"]
    all_transfers = set(app.transfers.keys())
    required_transfers = {k for k in all_transfers if app.transfers[k].required}
    provided = set(transfers.keys())
    validate_set(all_transfers, required_transfers, provided)

    transfer_dicts = {}
    for name in transfers:
        try:
            loc, path = transfers[name].split(":")
        except ValueError:
            raise click.BadParameter("Transfers must take the form LOCATION_ALIAS:PATH")
        transfer_dicts[name] = {"location_alias": loc, "path": path}
    return transfer_dicts


def validate_parents(ctx: Any, param: Any, value: List[int]) -> List[int]:
    client = ctx.obj.client
    parent_ids = value
    if not parent_ids:
        return []
    jobs = list(client.Job.objects.filter(id=parent_ids))
    if len(jobs) < len(parent_ids):
        job_ids = [j.id for j in jobs]
        missing_ids = [i for i in parent_ids if i not in job_ids]
        raise click.BadParameter(f"Could not find parent job ids {missing_ids}")
    return parent_ids


@job.command()
@click.option("-w", "--workdir", required=True, type=str)
@click.option("-a", "--app", required=True, type=str, callback=validate_app)
@click.option("-tag", "--tag", "tags", multiple=True, type=str, callback=validate_tags)
@click.option("-p", "--param", "parameters", multiple=True, type=str)
@click.option("-n", "--num-nodes", default=1, type=int)
@click.option("-rpn", "--ranks-per-node", default=1, type=int)
@click.option("-tpr", "--threads-per-rank", default=1, type=int)
@click.option("-tpc", "--threads-per-core", default=1, type=int)
@click.option("-g", "--gpus-per-rank", default=0, type=float)
@click.option("-npc", "--node-packing-count", default=1, type=int)
@click.option(
    "-lp",
    "--launch-param",
    "launch_params",
    multiple=True,
    type=str,
    callback=validate_tags,
)
@click.option("-t", "--wall-time-min", default=1, type=int)
@click.option(
    "-pid",
    "--parent-id",
    "parent_ids",
    multiple=True,
    type=int,
    callback=validate_parents,
)
@click.option(
    "-s",
    "--stage-data",
    "transfers",
    multiple=True,
    type=str,
    callback=validate_transfers,
)
@click.pass_context
def create(
    ctx: Any,
    workdir: str,
    app: "App",
    tags: List[str],
    parameters: List[str],
    num_nodes: int,
    ranks_per_node: int,
    threads_per_rank: int,
    threads_per_core: int,
    gpus_per_rank: int,
    node_packing_count: int,
    launch_params: List[str],
    wall_time_min: int,
    parent_ids: List[int],
    transfers: List[str],
) -> None:
    """
    Add a new Balsam Job to run an App at this Site
    """
    client = ctx.obj.client
    if Path(workdir).is_absolute():
        raise click.BadParameter("workdir must be a relative path: cannot start with '/'")
    parameters_dict = validate_parameters(parameters, app)
    job = client.Job(
        workdir=workdir,
        app_id=app.id,
        tags=tags,
        parameters=parameters_dict,
        num_nodes=num_nodes,
        ranks_per_node=ranks_per_node,
        threads_per_rank=threads_per_rank,
        threads_per_core=threads_per_core,
        gpus_per_rank=gpus_per_rank,
        node_packing_count=node_packing_count,
        launch_params=launch_params,
        wall_time_min=wall_time_min,
        parent_ids=parent_ids,
        transfers=transfers,
    )
    click.echo(yaml.dump(job.display_dict(), sort_keys=False, indent=4))
    if click.confirm("Do you want to create this Job?"):
        job.save()
        click.echo(f"Added Job id={job.id}")


@job.command()
@click.option("-t", "--tag", "tags", multiple=True, type=str, callback=validate_tags)
@click.option("-s", "--state", type=str, callback=validate_state)
@click.option("-ns", "--exclude-state", type=str, callback=validate_state)
@click.option("-w", "--workdir", type=str)
@click.option("-v", "--verbose", is_flag=True)
@click.pass_context
def ls(ctx: Any, tags: List[str], state: str, exclude_state: str, workdir: str, verbose: bool) -> None:
    """
    List Balsam Jobs
    """
    client = ctx.obj.client
    site_id = ctx.obj.settings.site_id
    jobs = client.Job.objects.filter(site_id=site_id)
    if tags:
        jobs = jobs.filter(tags=tags)
    if state:
        jobs = jobs.filter(state=state)
    if exclude_state:
        jobs = jobs.filter(state__ne=exclude_state)
    if workdir:
        jobs = jobs.filter(workdir__contains=workdir)

    result = list(jobs)
    if verbose:
        for j in result:
            click.echo(yaml.dump(j.display_dict(), sort_keys=False, indent=4))
            click.echo("---\n")
    else:
        click.echo(f"{'ID':5}   {'Job Dir':14}   {'State':16}   {'Tags':40}")
        for j in result:
            click.echo(f"{j.id:5d}   {j.workdir.as_posix():14}   {j.state:16}   {str(j.tags):40}")


@job.command()
@click.option("-i", "--id", "job_ids", multiple=True, type=int)
@click.option("-t", "--tag", "tags", multiple=True, type=str, callback=validate_tags)
@click.pass_context
def rm(ctx: Any, job_ids: List[int], tags: List[str]) -> None:
    """
    Remove Jobs
    """
    site_id = ctx.obj.settings.site_id
    client = ctx.obj.client
    jobs = client.Job.objects.filter(site_id=site_id)
    if job_ids:
        jobs = jobs.filter(id=job_ids)
    elif tags:
        jobs = jobs.filter(tags=tags)
    else:
        raise click.BadParameter("Provide either list of Job ids or tags to delete")
    count = jobs.count()
    if count < 1:
        click.echo("No jobs match deletion query")
    elif click.confirm(f"Really delete {count} jobs?"):
        jobs.delete()
