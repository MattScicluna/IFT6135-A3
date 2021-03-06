import torch
import numpy as np
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.init import xavier_uniform
from torch.autograd import Variable
from torch.nn import Parameter


class Controller(nn.Module):
    """Controller for NTM.
	"""

    def __init__(self, input_dim, output_dim, num_layers, batch_size):
        """network: object which takes as input r_t and x_t and returns h_t
        """
        super(Controller, self).__init__()
        self.input_dim = input_dim  # (8 + 1) + M*num_heads
        self.output_dim = output_dim  # 100
        self.num_layers = num_layers  # 1
        self.batch_size = batch_size  # batch size

    def reset_parameters(self):
        for param in self.parameters():
            if param.dim() == 1:
                nn.init.constant(param, 0)
            else:
                xavier_uniform(param)

    def size(self):
        """Returns the size of the controller
        """
        return self.input_dim, self.output_dim


class LSTMController(Controller):
    """LSTM controller for the NTM.
	"""

    def __init__(self, input_dim, output_dim, num_layers, batch_size, use_cuda):
        super(LSTMController, self).__init__(input_dim, output_dim, num_layers, batch_size)

        self.lstm = nn.LSTM(input_size=input_dim,
                            hidden_size=output_dim,
                            num_layers=num_layers)
        if use_cuda:
            self.lstm.cuda()

        # From https://github.com/fanxiao001/ift6135-assignment/blob/master/assignment3/NTM/controller.py
        self.lstm_h_bias = Parameter(torch.randn(self.num_layers, 1, self.output_dim) * 0.05)
        self.lstm_c_bias = Parameter(torch.randn(self.num_layers, 1, self.output_dim) * 0.05)

        self.reset_parameters()

    def forward(self, x, r, lstm_h, lstm_c):
        """forward pass of the LSTM controller
		"""
        #  Concatenate previous read state with input
        x = torch.cat((r, x.squeeze(0)), 1)

        # feed into controller with previous state
        output, state = self.lstm(x.unsqueeze(0), (lstm_h, lstm_c))
        return output, state

    def create_state(self, batch_size):
        # Dimension: (num_layers * num_directions, batch, hidden_size)
        # From https://github.com/fanxiao001/ift6135-assignment/blob/master/assignment3/NTM/controller.py
        lstm_h = self.lstm_h_bias.clone().repeat(1, batch_size, 1)
        lstm_c = self.lstm_c_bias.clone().repeat(1, batch_size, 1)
        return lstm_h, lstm_c


class MLPController(Controller):
    """MLP controller for the NTM.
	"""

    def __init__(self, input_dim, output_dim, num_layers, batch_size, use_cuda):
        super().__init__(input_dim, output_dim, num_layers, batch_size)

        self.mlp = nn.Linear(input_dim, output_dim)

        xavier_uniform(self.mlp.weight, gain=1)
        nn.init.normal(self.mlp.bias, std=0.01)

        if use_cuda:
            self.mlp.cuda()

    def forward(self, x, r):
        if self.batch_size == 1:
            x = torch.cat((r, x), 1)
        else:
            x = torch.cat((r, x.squeeze(0)), 1)
        output = self.mlp(x.unsqueeze(0))
        return output.squeeze(0)


class NTMReadHead(nn.Module):
    def __init__(self, use_cuda, memory_feature_size, saved_biases=False, folder=None):
        super(NTMReadHead, self).__init__()
        self.use_cuda = use_cuda
        self.memory_feature_size = memory_feature_size
        # self.register_parameter('read_bias', Parameter(torch.randn(1, self.memory_feature_size) * 0.01))
        if saved_biases:
            read_bias_np = np.load('biases/' + folder + '/read_bias.npy')
            self.read_bias = Variable(torch.from_numpy(read_bias_np))
        else:
            self.read_bias = Variable(torch.randn(1, self.memory_feature_size) * 0.01)
            np.save('biases/' + folder + '/read_bias.npy', self.read_bias.data.numpy())

    def forward(self, w, memory):
        """
		1) Expects memory to be a (batch_size x N x M) matrix, with N being
		the number of locations and M being the dimension of each stored feature.
		2) Expects weight, w, to be a (batch_size x N) vector representing the weighting on each
		memory row vector.

		output 'r' is a vector (batch_size x M)
		"""
        return torch.matmul(w.unsqueeze(1), memory).squeeze(1)

    def create_state(self, batch_size):
        random_init = self.read_bias.clone().repeat(batch_size, 1)
        if self.use_cuda:
            return random_init.cuda()
        return random_init


class NTMWriteHead(nn.Module):
    def __init__(self, use_cuda):
        super(NTMWriteHead, self).__init__()
        self.use_cuda = use_cuda

    def forward(self, w, memory, params):
        """
		1) Expects memory to be a (batch_size x N x M) matrix, with N being
		the number of locations and M being the dimension of each stored feature.
		2) Expects weight, w, to be a (batch_size x N) matrix representing the weighting on each
		memory row vector.
		3) Expects erase vector 'e' (from params dict) to be a (batch_size x M)
		matrix with each row being the strength with which we want to erase from memory.
		4) Expects add vector 'a' (from params dict) to be a (batch_size x M) matrix with
		each row being the strength with which we want to add to memory.
		"""
        e = params['e']
        a = params['a']
        prev_memory = memory
        mem_size = prev_memory.size()
        # I believe we need to Variable 'memory'. Not sure...
        memory = Variable(torch.Tensor(mem_size[0], mem_size[1], mem_size[2]))
        if self.use_cuda:
            memory = memory.cuda()
        for example in range(len(memory)):  # since first dim is batch_size
            memory[example] = prev_memory[example] * (1 - torch.ger(w[example], e[example]))  # Erase
            memory[example] = memory[example] + torch.ger(w[example], a[example])  # Add

        return memory


class NTMAttention(nn.Module):
    def __init__(self, use_cuda):
        super(NTMAttention, self).__init__()
        self.use_cuda = use_cuda

    def measure_similarity(self, k, beta, memory):
        k = k.unsqueeze(1)  # puts it in batch_size x 1 x M
        w = F.softmax(beta * F.cosine_similarity(memory + 1e-12, k + 1e-12, dim=-1))
        return w

    def interpolate(self, w_prev, w_c, g):
        return g * w_c + (1 - g) * w_prev

    def shift(self, w_g, s, int_shift):
        result = Variable(torch.zeros(w_g.size()))
        if self.use_cuda:
            result = result.cuda()
        for b in range(self.batch_size):
            result[b] = self.shift_convolve(w_g[b], s[b], int_shift)
        return result

    def shift_convolve(self, w_s, s, int_shift):
        assert s.size(0) == int_shift
        t = torch.cat([w_s[-2:], w_s, w_s[:2]])
        c = F.conv1d(t.view(1, 1, -1), s.view(1, 1, -1)).view(-1)
        return c[1:-1]

    def sharpen(self, w_hat, gamma):
        w = w_hat ** gamma
        w = torch.div(w, torch.sum(w, dim=1).view(-1, 1) + 1e-12)
        return w

    def forward(self, params, w_prev, memory, int_shift):
        """
		1) Expects params dict to extract
			- Beta (batch_size x scalar)
			- key (batch_size x M (feature size length))
			- gamma (batch_size x scalar)
			- g (batch_size x scalar)
			- shift (batch_size x M (feature size length))
		2) the previous step's weighting (batch_size x N)
		3) the current memory matrix (batch_size x N x M)

		Outputs new weighting (batch_size x N)
		"""
        self.beta = params['beta']
        self.key = params['kappa']
        self.gamma = params['gamma']
        self.g = params['g']
        self.s = params['s']
        self.batch_size = len(memory)

        w_c = self.measure_similarity(self.key, self.beta, memory)
        w_g = self.interpolate(w_prev, w_c, self.g)
        w_hat = self.shift(w_g, self.s, int_shift)
        weight = self.sharpen(w_hat, self.gamma)

        return weight


class NTM(nn.Module):
    """
	Neural Turing Machine
	"""

    def __init__(self, num_inputs, num_outputs, batch_size, controller_size,
                 controller_type, controller_layers, memory_size, memory_feature_size,
                 integer_shift, use_cuda, saved_biases=False):
        """Initialize the NTM.
		:param num_inputs: External input size.
		:param num_outputs: External output size.
		:param batch_Size: batch size.
		:param controller_size: size of controller output layer
		:param controller_type: controller network type (LSTM or MLP)
		:param controller_layers: number of layers of controller network
		:param memory_size: N in the paper
		:param memory_feature_size: M in the paper
		:param integer_shift: allowed integer shift (see pg 8 of paper)
		:param use_cuda: use cuda
		"""
        super(NTM, self).__init__()

        self.num_inputs = num_inputs
        self.num_outputs = num_outputs
        self.batch_size = batch_size
        self.controller_size = controller_size
        self.controller_type = controller_type
        self.controller_layers = controller_layers
        self.memory_size = memory_size
        self.memory_feature_size = memory_feature_size
        self.integer_shift = integer_shift
        self.use_cuda = use_cuda

        #  Initialize components
        if self.controller_type == 'LSTM':
            self.controller = LSTMController(input_dim=self.num_inputs + self.memory_feature_size,
                                             output_dim=self.controller_size, num_layers=controller_layers,
                                             batch_size=self.batch_size, use_cuda=self.use_cuda)
        elif self.controller_type == 'MLP':
            self.controller = MLPController(input_dim=self.num_inputs + self.memory_feature_size,
                                            output_dim=self.controller_size, num_layers=controller_layers,
                                            batch_size=self.batch_size, use_cuda=self.use_cuda)
        if self.controller_type == "LSTM":
            folder = 'ntm-lstm'
        else:
            folder = 'ntm-mlp'

        self.attention = NTMAttention(use_cuda=self.use_cuda)
        self.read_head = NTMReadHead(use_cuda=self.use_cuda,
                                     memory_feature_size=self.memory_feature_size,
                                     saved_biases=saved_biases, folder=folder)
        self.write_head = NTMWriteHead(use_cuda=self.use_cuda)

        #  Initialize memory
        # self.register_parameter('mem_bias', Parameter(torch.Tensor(self.memory_size, self.memory_feature_size)))
        if saved_biases:
            mem_bias_np = np.load('biases/' + folder + '/mem_bias.npy')
            self.mem_bias = Variable(torch.from_numpy(mem_bias_np))
        else:
            self.mem_bias = Variable(torch.Tensor(self.memory_size, self.memory_feature_size))
            stdev = 1 / (np.sqrt(self.memory_size + self.memory_feature_size))
            nn.init.uniform(self.mem_bias, -stdev, stdev)
            np.save('biases/' + folder + '/mem_bias.npy', self.mem_bias.data.numpy())

        # Initialize memory with mem_bias
        self.init_memory()

        #  Initialize weights
        self.init_headweights()

        # Initialize a fully connected layer to produce the actual output:
        self.fc = nn.Linear(self.controller_size + self.memory_feature_size, self.num_outputs)
        xavier_uniform(self.fc.weight, gain=1)

        # Corresponding to beta, kappa, gamma, g, s, e, a sizes from the paper
        self.params = ['beta', 'kappa', 'gamma', 'g', 's', 'e', 'a']
        self.params_lengths = [1, self.memory_feature_size, 1, 1, self.integer_shift,
                               self.memory_feature_size, self.memory_feature_size]

        self.fc_params_read = nn.Linear(self.controller_size, sum(self.params_lengths[:-2]))
        xavier_uniform(self.fc_params_read.weight, gain=1)
        nn.init.normal(self.fc_params_read.bias, std=0.01)

        self.fc_params_write = nn.Linear(self.controller_size, sum(self.params_lengths))
        xavier_uniform(self.fc_params_write.weight, gain=1)
        nn.init.normal(self.fc_params_write.bias, std=0.01)

        # Corresponding to beta, kappa, gamma, g, s, e, a
        # (choice of activations selected to obey corresponding domain restrictions from the paper)
        self.activations = {'beta': F.softplus,
                            'kappa': lambda x: x,
                            'gamma': lambda x: 1 + F.softplus(x),
                            'g': F.sigmoid,
                            's': lambda x: F.softmax(F.softplus(x)),
                            'e': F.sigmoid,
                            'a': F.sigmoid}

        if self.use_cuda:
            self.cuda()

    def init_headweights(self):
        self.weight_r = Variable(torch.zeros(self.batch_size, self.memory_size))
        self.weight_w = Variable(torch.zeros(self.batch_size, self.memory_size))
        if self.use_cuda:
            self.weight_r = self.weight_r.cuda()
            self.weight_w = self.weight_w.cuda()

        # For visualizing heads
        self.read_heads = []
        self.write_heads = []

    def init_memory(self):
        self.memory = self.mem_bias.clone().repeat(self.batch_size, 1, 1)
        if self.use_cuda:
            self.memory = self.memory.cuda()

    def convert_to_params(self, output, mode='read'):
        """Transform output from controller into parameters for attention and write heads
		:param output: output from controller.
		"""

        if mode == 'read':
            to_return = {'beta': 0,
                         'kappa': 0,
                         'gamma': 0,
                         'g': 0,
                         's': 0}
            o = self.fc_params_read(output)
            if self.batch_size == 1 and self.controller_type == 'MLP':
                o = o.unsqueeze(0)
            l = np.cumsum([0] + self.params_lengths[:-2])
            for idx in range(len(l) - 1):
                to_return[self.params[idx]] = self.activations[self.params[idx]](o.squeeze(0)[:, l[idx]:l[idx + 1]])

        elif mode == 'write':
            to_return = {'beta': 0,
                         'kappa': 0,
                         'gamma': 0,
                         'g': 0,
                         's': 0,
                         'e': 0,
                         'a': 0}
            o = self.fc_params_write(output)
            if self.batch_size == 1 and self.controller_type == 'MLP':
                o = o.unsqueeze(0)
            l = np.cumsum([0] + self.params_lengths)
            for idx in range(len(l) - 1):
                to_return[self.params[idx]] = self.activations[self.params[idx]](o.squeeze(0)[:, l[idx]:l[idx + 1]])

        return to_return

    def forward(self, x, r, lstm_h=None, lstm_c=None, vis_heads=False):
        """Perform forward pass from the NTM.
		:param x: current input.
		:param r: previous read head output.
		:param lstm_h: LSTM previous hidden state for controller (None if using MLP)
		:param lstm_c: LSTM previous memory state for controller (None if using MLP)
		"""
        if self.controller_type == 'LSTM':
            o, state = self.controller.forward(x.unsqueeze(0), r, lstm_h, lstm_c)
            lstm_h, lstm_c = state
        else:
            o = self.controller.forward(x, r)

        read_params = self.convert_to_params(o, mode='read')
        self.weight_r = self.attention.forward(read_params, self.weight_r, self.memory, self.integer_shift)
        next_r = self.read_head.forward(self.weight_r, self.memory)
        write_params = self.convert_to_params(o, mode='write')
        self.weight_w = self.attention.forward(write_params, self.weight_w, self.memory, self.integer_shift)
        self.memory = self.write_head.forward(self.weight_w, self.memory, write_params)

        # recording heads for visualization
        if vis_heads:
            self.read_heads += [self.weight_r]
            self.write_heads += [self.weight_w]

        # Generate Output
        if self.batch_size == 1 and self.controller_type == 'MLP':
            o = o.unsqueeze(0)
        o_r = torch.cat((o.squeeze(0), next_r), 1)
        output = F.sigmoid(self.fc(o_r))

        if self.controller_type == 'LSTM':
            return output, next_r, lstm_h, lstm_c

        return output, next_r
