# Training Loop Blocker

This repository does not contain a real machine-learning training loop.

## What this repository is

`nemoclaw` is a deployment, orchestration, and validation repository for a
three-node NemoClaw / OpenShell home-lab setup. Its executable surfaces are:

- infrastructure validation in `tests/`
- orchestration/runtime code in `orchestrator/`
- deployment and operations documentation in `README.md` and `docs/`

## What is not present

A repository-wide search found no legitimate training subsystem. There is no:

- training entrypoint such as `train.py`, `trainer.py`, or equivalent
- model fit loop
- optimizer / scheduler wiring
- dataloader pipeline for model training
- checkpoint/resume path for model training
- dataset preparation path for a training job

The only hit for the word `checkpoint` is an approvals/security explanation in
documentation, not a model-training checkpoint implementation.

## Practical consequence

You cannot come back tomorrow and start an "actual training loop" from this
repository, because the required training code does not exist here.

## Exact missing component

The missing component is a separate training repository or a new training module
that contains:

- the model-training code
- training configuration
- dataset wiring
- checkpoint/resume behavior
- the real launch command

## Useful commands for this repository tomorrow

If your goal tomorrow is to continue work on this repository itself, the
meaningful commands are:

```bash
cd tests
uv sync
uv run pytest -v
```

```bash
source orchestrator-env/bin/activate
python -m orchestrator health
python -m orchestrator status
```

If your goal is actual model training, use the correct training repository
instead of `nemoclaw`.
