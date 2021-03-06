#!/usr/bin/python3.6 -tt

import os, sys, argparse, random, gzip, keras, time, datetime, itertools
import pickle, numpy as np, pandas as pd
from chip_db import chip_data
from IntegratedGradients import *


def progress_bar(pct, size=100):
	# Returns a progress bar of how much some process has completed
	bar = "\r{:5.1f}".format(float(pct) * 100) + "% |"
	num_fill = int(pct * size)
	bar += '=' * (num_fill) + '>' + ' ' * (size-num_fill) + '|'
	if pct >= 1:
		bar += '\n'
	return bar


def rand_argmax(vector):
    # Returns the argmax of a vector, unless there's a tie, in which case it chooses one of the tied at random
    max_val = np.max(vector)
    idxs = np.where(vector == max_val)
    return np.random.choice(idxs[0])


def pad(matrix, axis=0, left_pad=None, right_pad=None, total_pad=None):
	# Pads a matrix along a given axis with zeros (or whatever)
	input_shape = list(np.shape(matrix))
	if total_pad:
		left_pad = int(total_pad) / 2
		right_pad = total_pad - left_pad
	left_shape = input_shape[:]
	left_shape[axis] = left_pad
	right_shape = input_shape[:]
	right_shape[axis] = right_pad
	return np.concatenate([np.zeros(left_shape), matrix, np.zeros(right_shape)], axis=axis)


def prep_sample(datapoint, total_pad=20):
	# Takes a datapoint from a data matrix, reverses the direction of the right matrix, 
	# pads it, and swaps axes
	if total_pad:
		data_left = pad(datapoint[0, :, :], axis=1, total_pad=total_pad)
		data_right = pad(datapoint[1, :, ::-1], axis=1, total_pad=total_pad)
		return [np.swapaxes(data_left, 0, 1), np.swapaxes(data_right, 0, 1)]
	return [np.swapaxes(datapoint[0], 0, 1), np.swapaxes(datapoint[1, :, ::-1], 0, 1)]


def unprep_sample(gradients, total_pad=20):
	# Takes in a sample of shape (2, 270, 148), should return (2, 148, 250)
	grad_left = gradients[0]
	grad_right = gradients[1][::-1,:]
	if total_pad:
		return [np.swapaxes(grad_left[total_pad/2:-(total_pad/2), :], 0, 1), 
				np.swapaxes(grad_right[total_pad/2:-(total_pad/2), :], 0, 1)]
	return [np.swapaxes(grad_left, 0, 1), 
				np.swapaxes(grad_right, 0, 1)]


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--interaction', '-i', help='name of interaction file')
	parser.add_argument('--data', '-d', help='name of numpy data file')
	parser.add_argument('--model', '-m', help='name of learned model file')
	parser.add_argument('--suff', '-s', help='additional suffix to use in naming files', default='.')
	parser.add_argument('--out_dir', '-o', help='directory in which to save files', default='.')
	parser.add_argument('--idxs', '-x', help='interaction numbers, blank for all', default=[-1, -1], type=int, nargs=2)
	parser.add_argument('--pad_size', '-w', help='size of padding', default=0, type=int)
	parser.add_argument('--resolution', help='resolution of data', default=100, type=int)
	#
	args = parser.parse_args()
	if args.out_dir[-1] != '/':
		args.out_dir += '/'
	if args.suff[0] != '.':
		args.suff = '.' + args.suff
	if args.suff[-1] != '.':
		args.suff += '.'
	if not os.path.exists(args.out_dir):
		os.makedirs(args.out_dir)
	#
	# Read interactions
	interactions = pd.read_csv(args.interaction, usecols=range(6), sep='\t', 
		names=['chrA', 'startA','endA','chrB','startB','endB'])
	#
	# Get data array
	sys.stderr.write('Reading data...')
	with open(args.data, 'rb') as infile:
		# data array is of shape (num_samples, 2, num_tracks, input_length)
		data_array = np.load(infile)
	sys.stderr.write('done\n')
	#
	num_interactions = np.shape(data_array)[0]
	if args.idxs == [-1, -1]:
		args.idxs = [0, num_interactions]
	#
	# Load model and IG wrapper
	model = keras.models.load_model(args.model)
	ig = integrated_gradients(model)
	#
	grad_mat = np.zeros((num_interactions, 2, np.shape(data_array[0][0])[0], 
		np.shape(data_array[0][0])[1]))
	# Loop over provided indices for narrowing
	for data_idx in range(args.idxs[0], args.idxs[1]):
		gradients = ig.explain(prep_sample(data_array[data_idx], total_pad=2*args.pad_size), 
				num_steps=50) # shape (intn_idx, 2, length, features)
		grad_mat[data_idx] = unprep_sample(gradients, total_pad=2*args.pad_size)
		#
		sys.stderr.write(progress_bar(float(data_idx-args.idxs[0]+1)/(args.idxs[1]-args.idxs[0])))
	#
	# Shape is (num_interactions, 2, length)
	importances = np.sum(grad_mat, axis=2)
	interaction_width = np.shape(importances)[-1] * args.resolution
	#
	# Write results to file
	sys.stderr.write('Saving...')
	sys.stderr.flush()
	np.save(args.out_dir+'gradients'+args.suff+'npy', grad_mat)
	np.save(args.out_dir+'importances'+args.suff+'npy', importances)
	with open(args.out_dir+'importances'+args.suff+'left.txt', 'w') as outfile:
		for row in importances[:, 0, :]:
			outfile.write('\t'.join([str(i) for i in row]) + '\n')
	with open(args.out_dir+'importances'+args.suff+'right.txt', 'w') as outfile:
		for row in importances[:, 1, :]:
			outfile.write('\t'.join([str(i) for i in row]) + '\n')
	with open(args.out_dir+'fine-mapping'+args.suff+'txt', 'w') as outfile:
		for idx, row in interactions.iterrows():
			chrA, startA, endA, chrB, startB, endB = row
			leftmost_A = (startA + endA - interaction_width) // 2
			leftmost_B = (startB + endB - interaction_width) // 2
			fine_map_idx_A = rand_argmax(importances[idx, 0])
			fine_map_idx_B = rand_argmax(importances[idx, 1])
			fine_map_pos_A = leftmost_A + (fine_map_idx_A * args.resolution)
			fine_map_pos_B = leftmost_B + (fine_map_idx_B * args.resolution)
			outfile.write('\t'.join([str(i) for i in [chrA, fine_map_pos_A, fine_map_pos_A+args.resolution,
													  chrB, fine_map_pos_B, fine_map_pos_B+args.resolution]]) + '\n')
	sys.stderr.write('done!\n')

if __name__ == '__main__':
	main()