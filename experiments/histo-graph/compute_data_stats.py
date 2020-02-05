#!/usr/bin/env python3
"""
Script for computing dataset statistics, e.g., avg number of node per class, image size
"""
import argparse

from histocartography.utils.io import read_params, check_for_dir, write_json, complete_path
from histocartography.dataloader.pascale_dataloader import make_data_loader
from histocartography.ml.models.constants import load_superpx_graph, load_cell_graph
from histocartography.utils.data_stats import DataStats


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-d',
        '--graph_data_path',
        type=str,
        help='path to the graph data.',
        default='../../data/',
        required=False
    )

    parser.add_argument(
        '-conf',
        '--config_fpath',
        type=str,
        help='path to the config file.',
        default='../../histocartography/config/concat_graph_model_config/concat_graph_model_config_0.json',
        required=False
    )

    parser.add_argument(
        '-o',
        '--out_folder',
        type=str,
        help='where to save the stats.',
        default='',
        required=False
    )

    return parser.parse_args()


def main(args):
    """
    Train HistoGraph.
    Args:
        args (Namespace): parsed arguments.
    """

    # load config file
    config = read_params(args.config_fpath, verbose=True)

    # make data loaders (train & validation)
    dataloaders, _ = make_data_loader(
        batch_size=1,
        num_workers=0,
        path=args.graph_data_path,
        config=config,
        load_cell_graph=load_cell_graph(config['model_type']),
        load_superpx_graph=False,
        load_image=False
    )

    data_stats = DataStats(
        cg_stats=load_cell_graph(config['model_type']),
        spx_stats=False,
        img_stats=False
    )

    out = data_stats(dataloaders)

    if args.out_folder:
        check_for_dir(args.out_folder)
        write_json(out, complete_path(args.out_folder, 'data_stats.json'))

    print('*** Data Statistics ***\n')
    for data_type, data in out.items():
        print('    *** Data Type {} ***'.format(data_type))
        for split, data_split in data.items():
            print('    * Split {} ***'.format(split))
            for cls, data_split_per_cls in data_split.items():
                print('    - Class {} ***'.format(cls))
                print(data_split_per_cls)
                print('\n')
            print('\n\n')
        print('\n\n\n')


if __name__ == "__main__":
    main(args=parse_arguments())
