from pathlib import Path
import pickle
import numpy as np
import argparse


def make_dataset(max_seq_length, num_sequences, vector_dim=8):
    """
    Make a dataset of random, vector_dim-dimension binary vectors concatenated with a binary
    indicator for the end of sequence, with sequences no longer than max_seq_length.
    """
    dataset = []
    vector_end = np.append(np.zeros(vector_dim), 1).reshape(vector_dim+1, 1)  # x_{T+1}

    for i in range(num_sequences):
        seq_length = np.random.randint(1, max_seq_length + 1)                 # T
        sequence = np.random.binomial(1, 0.5, size=(vector_dim, seq_length))  # 8 x T
        sequence = np.append(sequence, np.zeros((1, seq_length)), axis=0)     # Append 0 at the end of each vector
        sequence = np.append(sequence, vector_end, axis=1)                    # Append x_{T+1} at the end of the seq.
        dataset.append(sequence)
    return dataset


def split_dataset(dataset, train=0.8, val=0.1, test=0.1, path='data'):
    """
    Split dataset into train, validation and test sets.
    """
    num_sequences = len(dataset)
    train_idx = (0, int(train * num_sequences))
    val_idx = (train_idx[1], int(train * num_sequences + val * num_sequences))
    test_idx = (val_idx[1], -1)

    train_set = []
    val_set = []
    test_set = []

    random_indices = np.random.permutation(np.arange(num_sequences))

    train_set = [dataset[i] for i in random_indices[train_idx[0]:train_idx[1]]]
    val_set = [dataset[i] for i in random_indices[val_idx[0]:val_idx[1]]]
    test_set = [dataset[i] for i in random_indices[test_idx[0]:test_idx[1]]]

    # Save datasets
    Path.mkdir(Path('data'), exist_ok=True)

    with open('data/train.pkl', 'wb') as f:
        pickle.dump(train_set, f)
    with open('data/val.pkl', 'wb') as f:
        pickle.dump(val_set, f)
    with open('data/test.pkl', 'wb') as f:
        pickle.dump(test_set, f)


def make_test_different_T(max_T=100, n_inputs=20):
    """
    For point 2.c
    """
    Path.mkdir(Path('data/2c'), exist_ok=True)

    T_list = np.arange(10, max_T, 10)

    for T in T_list:
        dataset = make_dataset(T, n_inputs)

        with open('data/2c/T{}.pkl'.format(T), 'wb') as f:
            pickle.dump(dataset, f)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--T', '--max_length', default=20, type=int, help='Maximum sequence length')
    parser.add_argument('--n_seqs', type=int, default=30000, help='Number of seqs. in the dataset')
    parser.add_argument('--twoC', action='store_true', help='Only build point sets for point 2c')
    args = parser.parse_args()

    # Build general datase
    if not args.twoC:
        dataset = make_dataset(args.T, args.n_seqs)
        split_dataset(dataset)
    # Build test sets for point 2c)
    else:
        make_test_different_T()