from collections import defaultdict
from copy import deepcopy
import csv
from logging import Logger
import os
import sys
from typing import Callable, Dict, List, Tuple

import numpy as np
import pandas as pd

from .run_training import run_training
from chemprop.args import TrainArgs
from chemprop.constants import TEST_SCORES_FILE_NAME, TRAIN_LOGGER_NAME
from chemprop.data import get_data, get_task_names, MoleculeDataset, validate_dataset_type
from chemprop.go_utils import load_go_dag
from chemprop.utils import create_logger, makedirs, timeit


@timeit(logger_name=TRAIN_LOGGER_NAME)
def cross_validate(args: TrainArgs,
                   train_func: Callable[[TrainArgs, MoleculeDataset, Logger], Dict[str, List[float]]]
                   ) -> Tuple[float, float]:
    """
    Runs k-fold cross-validation.

    For each of k splits (folds) of the data, trains and tests a model on that split
    and aggregates the performance across folds.

    :param args: A :class:`~chemprop.args.TrainArgs` object containing arguments for
                 loading data and training the Chemprop model.
    :param train_func: Function which runs training.
    :return: A tuple containing the mean and standard deviation performance across folds.
    """
    logger = create_logger(name=TRAIN_LOGGER_NAME, save_dir=args.save_dir, quiet=args.quiet)
    if logger is not None:
        debug, info = logger.debug, logger.info
    else:
        debug = info = print

    # Initialize relevant variables
    init_seed = args.seed
    save_dir = args.save_dir
    args.task_names = get_task_names(
        path=args.data_path,
        smiles_column=args.smiles_column,
        target_columns=args.target_columns,
        ignore_columns=args.ignore_columns
    )

    # Print command line
    debug('Command line')
    debug(f'python {" ".join(sys.argv)}')

    # Print args
    debug('Args')
    debug(args)

    # Save args
    args.save(os.path.join(args.save_dir, 'args.json'))

    # Get data
    debug('Loading data')
    data = get_data(path=args.data_path, args=args, logger=logger, skip_none_targets=True)
    validate_dataset_type(data, dataset_type=args.dataset_type)
    args.features_size = data.features_size()
    debug(f'Number of tasks = {args.num_tasks}')

    # Load GO DAG if necessary
    if args.use_go_dag:
        debug('Loading GO DAG')
        args.go_dag = load_go_dag(go_obo_path=args.go_obo_path, go_ids=args.task_names)

        # Permute tasks so that they line up with the prediction order of the DAGModel
        task_names = args.task_names[1:] if args.organism_and_go else args.task_names
        permutation = sorted(range(len(task_names)), key=lambda i: args.go_dag.node_to_index(task_names[i]))
        permutation = [0] + [i + 1 for i in permutation] if args.organism_and_go else permutation
        data.permute_targets(permutation)
        args.task_names = [args.task_names[i] for i in permutation]

    # Run training on different random seeds for each fold
    all_scores = defaultdict(list)
    for fold_num in range(args.num_folds):
        info(f'Fold {fold_num}')
        args.seed = init_seed + fold_num
        args.save_dir = os.path.join(save_dir, f'fold_{fold_num}')
        makedirs(args.save_dir)
        model_scores = train_func(args, deepcopy(data), logger)  # deepcopy since data may be modified
        for metric, scores in model_scores.items():
            all_scores[metric].append(scores)
    all_scores = dict(all_scores)

    # Convert scores to numpy arrays
    for metric, scores in all_scores.items():
        all_scores[metric] = np.array(scores)

    # Report results
    info(f'{args.num_folds}-fold cross validation')

    # Report scores for each fold
    for fold_num in range(args.num_folds):
        for metric, scores in all_scores.items():
            info(f'\tSeed {init_seed + fold_num} ==> test {metric} = {np.nanmean(scores[fold_num]):.6f}')

            if args.show_individual_scores:
                for task_name, score in zip(args.task_names, scores[fold_num]):
                    info(f'\t\tSeed {init_seed + fold_num} ==> test {task_name} {metric} = {score:.6f}')

    # Report scores across folds
    for metric, scores in all_scores.items():
        avg_scores = np.nanmean(scores, axis=1)  # average score for each model across tasks
        mean_score, std_score = np.nanmean(avg_scores), np.nanstd(avg_scores)
        info(f'Overall test {metric} = {mean_score:.6f} +/- {std_score:.6f}')

        if args.show_individual_scores:
            for task_num, task_name in enumerate(args.task_names):
                info(f'\tOverall test {task_name} {metric} = '
                     f'{np.nanmean(scores[:, task_num]):.6f} +/- {np.nanstd(scores[:, task_num]):.6f}')

    # Save scores
    with open(os.path.join(save_dir, TEST_SCORES_FILE_NAME), 'w') as f:
        writer = csv.writer(f)

        header = ['Task']
        for metric in args.metrics:
            header += [f'Mean {metric}', f'Standard deviation {metric}'] + \
                      [f'Fold {i} {metric}' for i in range(args.num_folds)]
        writer.writerow(header)

        for task_num, task_name in enumerate(args.task_names):
            row = [task_name]
            for metric, scores in all_scores.items():
                task_scores = scores[:, task_num]
                mean, std = np.nanmean(task_scores), np.nanstd(task_scores)
                row += [mean, std] + task_scores.tolist()
            writer.writerow(row)

    # Determine mean and std score of main metric
    avg_scores = np.nanmean(all_scores[args.metric], axis=1)
    mean_score, std_score = np.nanmean(avg_scores), np.nanstd(avg_scores)

    # Optionally merge and save test preds
    if args.save_preds:
        all_preds = pd.concat([pd.read_csv(os.path.join(save_dir, f'fold_{fold_num}', 'test_preds.csv'))
                               for fold_num in range(args.num_folds)])
        all_preds.to_csv(os.path.join(save_dir, 'test_preds.csv'), index=False)

    return mean_score, std_score


def chemprop_train() -> None:
    """Parses Chemprop training arguments and trains (cross-validates) a Chemprop model.

    This is the entry point for the command line command :code:`chemprop_train`.
    """
    cross_validate(args=TrainArgs().parse_args(), train_func=run_training)
