"""Explicit private configuration for the separate graph-composition service."""
from ..dataset_runner.__main__ import load_config as read_private_config,main as command
from .service import serve


def load_config(path):
    return read_private_config(path,enabled_key='graph_dataset_enabled',delivery_key='graph_dataset_delivery_dir',
        additional_keys=('graph_dataset_enabled','graph_dataset_delivery_dir'),require_lock=True)


def main(argv=None):
    return command(argv,loader=load_config,server=serve)


if __name__=='__main__':raise SystemExit(main())
