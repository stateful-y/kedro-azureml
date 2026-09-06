"""Helpers for detecting distributed training environments."""

import json
import logging
import os

logger = logging.getLogger(__name__)

#: The variables Azure ML sets for an MPI or PyTorch distributed step, in the order they
#: are inspected: Open MPI's own rank first, then the generic ``RANK``.
RANK_VARS = ("OMPI_COMM_WORLD_RANK", "RANK")


def mpi_rank() -> int:
    """Return the 0-based rank of this process in an MPI or PyTorch step, else 0.

    Reads the variables in :data:`RANK_VARS` order. A variable that is set but
    does not hold an integer is skipped in favour of the next one, and 0 is
    returned when none parses, so a single-instance run and a malformed
    launcher variable both read as rank 0 instead of failing the step. Use it
    for anything that varies per rank, such as a seed offset or a log marker.

    Returns
    -------
    int
        The rank parsed from the first of :data:`RANK_VARS` that holds an
        integer, or 0.

    See Also
    --------
    [is_distributed_master_node][kedro_azureml_pipeline.distributed.utils.is_distributed_master_node] : Checks master rank, including the TensorFlow case.
    [is_distributed_environment][kedro_azureml_pipeline.distributed.utils.is_distributed_environment] : Detects distributed context.
    """
    for var in RANK_VARS:
        value = os.environ.get(var)
        if value is None:
            continue
        try:
            return int(value)
        except ValueError:
            continue
    return 0


def is_distributed_master_node() -> bool:
    """Check whether this process is the master node.

    Inspects ``TF_CONFIG``, ``OMPI_COMM_WORLD_RANK``, and ``RANK``
    environment variables.

    Returns
    -------
    bool
        ``True`` if this process is rank 0 or the detection fails.

    See Also
    --------
    [is_distributed_environment][kedro_azureml_pipeline.distributed.utils.is_distributed_environment] : Detects distributed context.
    [DistributedNodeConfig][kedro_azureml_pipeline.distributed.config.DistributedNodeConfig] : Per-node distributed config.
    """
    is_rank_0 = True
    try:
        if "TF_CONFIG" in os.environ:
            # TensorFlow
            tf_config = json.loads(os.environ["TF_CONFIG"])
            worker_type = tf_config["task"]["type"].lower()
            is_rank_0 = (worker_type in {"chief", "master"}) or (
                worker_type == "worker" and tf_config["task"]["index"] == 0
            )
        else:
            # MPI + PyTorch: the same variables, in the same order, as ``mpi_rank``. Unlike
            # there, a malformed value is an error here, handled below.
            for e in RANK_VARS:
                if e in os.environ:
                    is_rank_0 = int(os.environ[e]) == 0
                    break
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        logger.error(
            "Could not parse environment variables related to distributed computing. "
            "Set appropriate values for one of: RANK, OMPI_COMM_WORLD_RANK or TF_CONFIG",
            exc_info=True,
        )
        logger.warning("Assuming this node is not a master node, due to error.")
        return False
    return is_rank_0


def is_distributed_environment() -> bool:
    """Check whether the process is running in a distributed context.

    Returns
    -------
    bool
        ``True`` if any distributed rank variable is set.

    See Also
    --------
    [is_distributed_master_node][kedro_azureml_pipeline.distributed.utils.is_distributed_master_node] : Checks master rank.
    [mpi_rank][kedro_azureml_pipeline.distributed.utils.mpi_rank] : The rank itself.
    [DistributedNodeConfig][kedro_azureml_pipeline.distributed.config.DistributedNodeConfig] : Per-node distributed config.
    """
    return any(e in os.environ for e in (*RANK_VARS, "TF_CONFIG"))
