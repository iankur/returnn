
"""
Some datasets for artificially generated data.
"""

from __future__ import print_function

from Dataset import Dataset, DatasetSeq, convert_data_dims
from CachedDataset2 import CachedDataset2
from Util import class_idx_seq_to_1_of_k, CollectionReadCheckCovered, PY3
from Log import log
import numpy
import re
import sys
import typing


class GeneratingDataset(Dataset):
  """
  Some base class for datasets with artificially generated data.
  """

  _input_classes = None
  _output_classes = None

  def __init__(self, input_dim, output_dim, num_seqs=float("inf"), fixed_random_seed=None, **kwargs):
    """
    :param int|None input_dim:
    :param int|dict[str,int|(int,int)|dict] output_dim: if dict, can specify all data-keys
    :param int|float num_seqs:
    :param int fixed_random_seed:
    """
    super(GeneratingDataset, self).__init__(**kwargs)
    assert self.shuffle_frames_of_nseqs == 0

    self.num_inputs = input_dim
    output_dim = convert_data_dims(output_dim, leave_dict_as_is=False)
    if "data" not in output_dim and input_dim is not None:
      output_dim["data"] = (input_dim * self.window, 2)  # not sparse
    self.num_outputs = output_dim
    self.expected_load_seq_start = 0
    self._num_seqs = num_seqs
    self.random = numpy.random.RandomState(1)
    self.fixed_random_seed = fixed_random_seed  # useful when used as eval dataset
    self.reached_final_seq = False
    self.added_data = []  # type: typing.List[DatasetSeq]

  def init_seq_order(self, epoch=None, seq_list=None):
    """
    :type epoch: int|None
    :param seq_list: predefined order. doesn't make sense here
    This is called when we start a new epoch, or at initialization.
    """
    super(GeneratingDataset, self).init_seq_order(epoch=epoch)
    assert not seq_list, "predefined order doesn't make sense for %s" % self.__class__.__name__
    self.random.seed(self.fixed_random_seed or self._get_random_seed_for_epoch(epoch=epoch))
    self._num_timesteps = 0
    self.reached_final_seq = False
    self.expected_load_seq_start = 0
    self.added_data = []
    return True

  def _cleanup_old_seqs(self, seq_idx_end):
    i = 0
    while i < len(self.added_data):
      if self.added_data[i].seq_idx >= seq_idx_end:
        break
      i += 1
    del self.added_data[:i]

  def _check_loaded_seq_idx(self, seq_idx):
    if not self.added_data:
      raise Exception("no data loaded yet")
    start_loaded_seq_idx = self.added_data[0].seq_idx
    end_loaded_seq_idx = self.added_data[-1].seq_idx
    if seq_idx < start_loaded_seq_idx or seq_idx > end_loaded_seq_idx:
      raise Exception("seq_idx %i not in loaded seqs range [%i,%i]" % (
        seq_idx, start_loaded_seq_idx, end_loaded_seq_idx))

  def _get_seq(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: DatasetSeq|None
    """
    for data in self.added_data:
      if data.seq_idx == seq_idx:
        return data
    return None

  def is_cached(self, start, end):
    """
    :param int start:
    :param int end:
    :rtype: bool
    """
    # Always False, to force that we call self._load_seqs().
    # This is important for our buffer management.
    return False

  def _load_seqs(self, start, end):
    """
    :param int start: inclusive seq idx start
    :param int end: exclusive seq idx end
    """
    # We expect that start increase monotonic on each call
    # for not-yet-loaded data.
    # This will already be called with _load_seqs_superset indices.
    assert start >= self.expected_load_seq_start
    if start > self.expected_load_seq_start:
      # Cleanup old data.
      self._cleanup_old_seqs(start)
      self.expected_load_seq_start = start
    if self.added_data:
      start = max(self.added_data[-1].seq_idx + 1, start)
    if end > self.num_seqs:
      end = self.num_seqs
    if end >= self.num_seqs:
      self.reached_final_seq = True
    seqs = [self.generate_seq(seq_idx=seq_idx) for seq_idx in range(start, end)]
    if self.window > 1:
      for seq in seqs:
        seq.features["data"] = self.sliding_window(seq.features["data"])
    self._num_timesteps += sum([seq.num_frames for seq in seqs])
    self.added_data += seqs

  def generate_seq(self, seq_idx):
    """
    :type seq_idx: int
    :rtype: DatasetSeq
    """
    raise NotImplementedError

  def _shuffle_frames_in_seqs(self, start, end):
    assert False, "Shuffling in GeneratingDataset does not make sense."

  def get_num_timesteps(self):
    """
    :rtype: int
    """
    assert self.reached_final_seq
    return self._num_timesteps

  @property
  def num_seqs(self):
    """
    :rtype: int
    """
    return self._num_seqs

  def get_seq_length(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: Util.NumbersDict
    """
    # get_seq_length() can be called before the seq is loaded via load_seqs().
    # Thus, we just call load_seqs() ourselves here.
    assert seq_idx >= self.expected_load_seq_start
    self.load_seqs(self.expected_load_seq_start, seq_idx + 1)
    return self._get_seq(seq_idx).num_frames

  def get_data(self, seq_idx, key):
    """
    :param int seq_idx:
    :param str key:
    :rtype: numpy.ndarray
    """
    return self._get_seq(seq_idx).features[key]

  def get_input_data(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: numpy.ndarray
    """
    return self.get_data(seq_idx, "data")

  def get_targets(self, target, seq_idx):
    """
    :param int seq_idx:
    :param str target:
    :rtype: numpy.ndarray
    """
    return self.get_data(seq_idx, target)

  def get_ctc_targets(self, sorted_seq_idx):
    """
    :param int sorted_seq_idx:
    :rtype: typing.Optional[numpy.ndarray]
    """
    self._check_loaded_seq_idx(sorted_seq_idx)
    assert self._get_seq(sorted_seq_idx).ctc_targets

  def get_tag(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: str
    """
    self._check_loaded_seq_idx(seq_idx)
    return self._get_seq(seq_idx).seq_tag


class Task12AXDataset(GeneratingDataset):
  """
  12AX memory task.
  This is a simple memory task where there is an outer loop and an inner loop.
  Description here: http://psych.colorado.edu/~oreilly/pubs-abstr.html#OReillyFrank06
  """

  _input_classes = "123ABCXYZ"
  _output_classes = "LR"

  def __init__(self, **kwargs):
    super(Task12AXDataset, self).__init__(
      input_dim=len(self._input_classes),
      output_dim=len(self._output_classes),
      **kwargs)

  def get_random_seq_len(self):
    """
    :rtype: int
    """
    return self.random.randint(10, 100)

  def generate_input_seq(self, seq_len):
    """
    Somewhat made up probability distribution.
    Try to make in a way that at least some "R" will occur in the output seq.
    Otherwise, "R"s are really rare.

    :param int seq_len:
    :rtype: list[int]
    """
    seq = self.random.choice(["", "1", "2"])
    while len(seq) < seq_len:
      if self.random.uniform() < 0.5:
        seq += self.random.choice(list("12"))
      if self.random.uniform() < 0.9:
        seq += self.random.choice(["AX", "BY"])
      while self.random.uniform() < 0.5:
        seq += self.random.choice(list(self._input_classes))
    return list(map(self._input_classes.index, seq[:seq_len]))

  @classmethod
  def make_output_seq(cls, input_seq):
    """
    :type input_seq: list[int]
    :rtype: list[int]
    """
    outer_state = ""
    inner_state = ""
    input_classes = cls._input_classes
    output_seq_str = ""
    for i in input_seq:
      c = input_classes[i]
      o = "L"
      if c in "12":
        outer_state = c
      elif c in "AB":
        inner_state = c
      elif c in "XY":
        if outer_state + inner_state + c in ["1AX", "2BY"]:
          o = "R"
        inner_state = ""
      # Ignore other cases, "3CZ".
      output_seq_str += o
    return list(map(cls._output_classes.index, output_seq_str))

  def estimate_output_class_priors(self, num_trials, seq_len=10):
    """
    :type num_trials: int
    :param int seq_len:
    :rtype: (float, float)
    """
    count_l, count_r = 0, 0
    for i in range(num_trials):
      input_seq = self.generate_input_seq(seq_len)
      output_seq = self.make_output_seq(input_seq)
      count_l += output_seq.count(0)
      count_r += output_seq.count(1)
    return float(count_l) / (num_trials * seq_len), float(count_r) / (num_trials * seq_len)

  def generate_seq(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: DatasetSeq
    """
    seq_len = self.get_random_seq_len()
    input_seq = self.generate_input_seq(seq_len)
    output_seq = self.make_output_seq(input_seq)
    features = class_idx_seq_to_1_of_k(input_seq, num_classes=len(self._input_classes))
    targets = numpy.array(output_seq)
    return DatasetSeq(seq_idx=seq_idx, features=features, targets=targets)


class TaskEpisodicCopyDataset(GeneratingDataset):
  """
  Episodic Copy memory task.
  This is a simple memory task where we need to remember a sequence.
  Described in: http://arxiv.org/abs/1511.06464
  Also tested for Associative LSTMs.
  This is a variant where the lengths are random, both for the chars and for blanks.
  """

  # Blank, delimiter and some chars.
  _input_classes = " .01234567"
  _output_classes = _input_classes

  def __init__(self, **kwargs):
    super(TaskEpisodicCopyDataset, self).__init__(
      input_dim=len(self._input_classes),
      output_dim=len(self._output_classes),
      **kwargs)

  def generate_input_seq(self):
    """
    :rtype: list[int]
    """
    seq = ""
    # Start with random chars.
    rnd_char_len = self.random.randint(1, 10)
    seq += "".join([self.random.choice(list(self._input_classes[2:]))
                    for _ in range(rnd_char_len)])
    blank_len = self.random.randint(1, 100)
    seq += " " * blank_len  # blanks
    seq += "."  # 1 delim
    seq += "." * (rnd_char_len + 1)  # we wait for the outputs + 1 delim
    return list(map(self._input_classes.index, seq))

  @classmethod
  def make_output_seq(cls, input_seq):
    """
    :type input_seq: list[int]
    :rtype: list[int]
    """
    input_classes = cls._input_classes
    input_mem = ""
    output_seq_str = ""
    state = 0
    for i in input_seq:
      c = input_classes[i]
      if state == 0:
        output_seq_str += " "
        if c == " ":
          pass  # just ignore
        elif c == ".":
          state = 1  # start with recall now
        else:
          input_mem += c
      else:  # recall from memory
        # Ignore input.
        if not input_mem:
          output_seq_str += "."
        else:
          output_seq_str += input_mem[:1]
          input_mem = input_mem[1:]
    return list(map(cls._output_classes.index, output_seq_str))

  def generate_seq(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: DatasetSeq
    """
    input_seq = self.generate_input_seq()
    output_seq = self.make_output_seq(input_seq)
    features = class_idx_seq_to_1_of_k(input_seq, num_classes=len(self._input_classes))
    targets = numpy.array(output_seq)
    return DatasetSeq(seq_idx=seq_idx, features=features, targets=targets)


class TaskXmlModelingDataset(GeneratingDataset):
  """
  XML modeling memory task.
  This is a memory task where we need to remember a stack.
  Defined in Jozefowicz et al. (2015).
  Also tested for Associative LSTMs.
  """

  # Blank, XML-tags and some chars.
  _input_classes = " <>/abcdefgh"
  _output_classes = _input_classes

  def __init__(self, limit_stack_depth=4, **kwargs):
    super(TaskXmlModelingDataset, self).__init__(
      input_dim=len(self._input_classes),
      output_dim=len(self._output_classes),
      **kwargs)
    self.limit_stack_depth = limit_stack_depth

  def generate_input_seq(self):
    """
    :rtype: list[int]
    """
    # Because this is a prediction task, start with blank,
    # and the output seq should predict the next char after the blank.
    seq = " "
    xml_stack = []
    while True:
      if not xml_stack or (len(xml_stack) < self.limit_stack_depth and self.random.rand() > 0.6):
        tag_len = self.random.randint(1, 10)
        tag = "".join([self.random.choice(list(self._input_classes[4:]))
                       for _ in range(tag_len)])
        seq += "<%s>" % tag
        xml_stack += [tag]
      else:
        seq += "</%s>" % xml_stack.pop()
      if not xml_stack and self.random.rand() > 0.2:
        break
    return list(map(self._input_classes.index, seq))

  @classmethod
  def make_output_seq(cls, input_seq):
    """
    :type input_seq: list[int]
    :rtype: list[int]
    """
    input_seq_str = "".join(cls._input_classes[i] for i in input_seq)
    xml_stack = []
    output_seq_str = ""
    state = 0
    for c in input_seq_str:
      if c in " >":
        output_seq_str += "<"  # We expect an open char.
        assert state != 1, repr(input_seq_str)
        state = 1  # expect beginning of tag
      elif state == 1:  # in beginning of tag
        output_seq_str += " "  # We don't know yet.
        assert c == "<", repr(input_seq_str)
        state = 2
      elif state == 2:  # first char in tag
        if c == "/":
          assert xml_stack, repr(input_seq_str)
          output_seq_str += xml_stack[-1][0]
          xml_stack[-1] = xml_stack[-1][1:]
          state = 4  # closing tag
        else:  # opening tag
          output_seq_str += " "  # We don't know yet.
          assert c not in " <>/", repr(input_seq_str)
          state = 3
          xml_stack += [c]
      elif state == 3:  # opening tag
        output_seq_str += " "  # We don't know.
        xml_stack[-1] += c
      elif state == 4:  # closing tag
        assert xml_stack, repr(input_seq_str)
        if not xml_stack[-1]:
          output_seq_str += ">"
          xml_stack.pop()
          state = 0
        else:
          output_seq_str += xml_stack[-1][0]
          xml_stack[-1] = xml_stack[-1][1:]
      else:
        assert False, "invalid state %i. input %r" % (state, input_seq_str)
    return list(map(cls._output_classes.index, output_seq_str))

  def generate_seq(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: DatasetSeq
    """
    input_seq = self.generate_input_seq()
    output_seq = self.make_output_seq(input_seq)
    features = class_idx_seq_to_1_of_k(input_seq, num_classes=len(self._input_classes))
    targets = numpy.array(output_seq)
    return DatasetSeq(seq_idx=seq_idx, features=features, targets=targets)


class TaskVariableAssignmentDataset(GeneratingDataset):
  """
  Variable Assignment memory task.
  This is a memory task to test for key-value retrieval.
  Defined in Associative LSTM paper.
  """

  # Blank/Delim/End, Store/Query, and some chars for key/value.
  _input_classes = " ,.SQ()abcdefgh"
  _output_classes = _input_classes

  def __init__(self, **kwargs):
    super(TaskVariableAssignmentDataset, self).__init__(
      input_dim=len(self._input_classes),
      output_dim=len(self._output_classes),
      **kwargs)

  def generate_input_seq(self):
    """
    :rtype: list[int]
    """
    seq = ""
    from collections import OrderedDict
    store = OrderedDict()
    # First the assignments.
    num_assignments = self.random.randint(1, 5)
    for i in range(num_assignments):
      key_len = self.random.randint(2, 5)
      while True:  # find unique key
        key = "".join([self.random.choice(list(self._input_classes[7:]))
                       for _ in range(key_len)])
        if key not in store:
          break
      value_len = self.random.randint(1, 2)
      value = "".join([self.random.choice(list(self._input_classes[7:]))
                       for _ in range(value_len)])
      if seq:
        seq += ","
      seq += "S(%s,%s)" % (key, value)
      store[key] = value
    # Now one query.
    key = self.random.choice(store.keys())
    value = store[key]
    seq += ",Q(%s)" % key
    seq += "%s." % value
    return list(map(self._input_classes.index, seq))

  @classmethod
  def make_output_seq(cls, input_seq):
    """
    :type input_seq: list[int]
    :rtype: list[int]
    """
    input_seq_str = "".join(cls._input_classes[i] for i in input_seq)
    store = {}
    key, value = "", ""
    output_seq_str = ""
    state = 0
    for c in input_seq_str:
      if state == 0:
        key = ""
        if c == "S":
          state = 1  # store
        elif c == "Q":
          state = 2  # query
        elif c in " ,":
          pass  # can be ignored
        else:
          assert False, "c %r in %r" % (c, input_seq_str)
        output_seq_str += " "
      elif state == 1:  # store
        assert c == "(", repr(input_seq_str)
        state = 1.1
        output_seq_str += " "
      elif state == 1.1:  # store.key
        if c == ",":
          assert key
          value = ""
          state = 1.5  # store.value
        else:
          assert c not in " .,SQ()", repr(input_seq_str)
          key += c
        output_seq_str += " "
      elif state == 1.5:  # store.value
        if c == ")":
          assert value
          store[key] = value
          state = 0
        else:
          assert c not in " .,SQ()", repr(input_seq_str)
          value += c
        output_seq_str += " "
      elif state == 2:  # query
        assert c == "(", repr(input_seq_str)
        state = 2.1
        output_seq_str += " "
      elif state == 2.1:  # query.key
        if c == ")":
          value = store[key]
          output_seq_str += value[0]
          value = value[1:]
          state = 2.5
        else:
          assert c not in " .,SQ()", repr(input_seq_str)
          key += c
          output_seq_str += " "
      elif state == 2.5:  # query result
        assert c not in " .,SQ()", repr(input_seq_str)
        if value:
          output_seq_str += value[0]
          value = value[1:]
        else:
          output_seq_str += "."
          state = 2.6
      elif state == 2.6:  # query result end
        assert c == ".", repr(input_seq_str)
        output_seq_str += " "
      else:
        assert False, "invalid state %i, input %r" % (state, input_seq_str)
    return list(map(cls._output_classes.index, output_seq_str))

  def generate_seq(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: DatasetSeq
    """
    input_seq = self.generate_input_seq()
    output_seq = self.make_output_seq(input_seq)
    features = class_idx_seq_to_1_of_k(input_seq, num_classes=len(self._input_classes))
    targets = numpy.array(output_seq)
    return DatasetSeq(seq_idx=seq_idx, features=features, targets=targets)


class TaskNumberBaseConvertDataset(GeneratingDataset):
  """
  Task: E.g: Get some number in octal and convert it to binary (e.g. "10101001").
  Or basically convert some number from some base into another base.
  """

  def __init__(self, input_base=8, output_base=2, min_input_seq_len=1, max_input_seq_len=8, **kwargs):
    """
    :param int input_base:
    :param int output_base:
    :param int min_input_seq_len:
    :param int max_input_seq_len:
    """
    super(TaskNumberBaseConvertDataset, self).__init__(
      input_dim=input_base,
      output_dim={"data": (input_base, 1), "classes": (output_base, 1)},
      **kwargs)
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    assert 2 <= input_base <= len(chars) and 2 <= output_base <= len(chars)
    self.input_base = input_base
    self.output_base = output_base
    self._input_classes = chars[:input_base]
    self._output_classes = chars[:output_base]
    self.labels = {"data": self._input_classes, "classes": self._output_classes}
    assert 0 < min_input_seq_len <= max_input_seq_len
    self.min_input_seq_len = min_input_seq_len
    self.max_input_seq_len = max_input_seq_len

  def get_random_input_seq_len(self):
    """
    :rtype: int
    """
    return self.random.randint(self.min_input_seq_len, self.max_input_seq_len + 1)

  def generate_input_seq(self):
    """
    :rtype: list[int]
    """
    seq_len = self.get_random_input_seq_len()
    seq = [self.random.randint(0, len(self._input_classes)) for _ in range(seq_len)]
    return seq

  def make_output_seq(self, input_seq):
    """
    :param list[int] input_seq:
    :rtype: list[int]
    """
    number = 0
    for i, d in enumerate(reversed(input_seq)):
      number += d * (self.input_base ** i)
    output_seq = []
    while number:
      output_seq.insert(0, number % self.output_base)
      number //= self.output_base
    if not output_seq:
      output_seq = [0]
    return output_seq

  def generate_seq(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: DatasetSeq
    """
    input_seq = self.generate_input_seq()
    output_seq = self.make_output_seq(input_seq)
    features = numpy.array(input_seq)
    targets = numpy.array(output_seq)
    return DatasetSeq(seq_idx=seq_idx, features=features, targets=targets)


class DummyDataset(GeneratingDataset):
  """
  Some dummy data, which does not have any meaning.
  If you want to have artificial data with some meaning, look at other datasets here.
  The input are some dense data, the outputs are sparse.
  """

  def __init__(self, input_dim, output_dim, num_seqs, seq_len=2,
               input_max_value=10.0, input_shift=None, input_scale=None, **kwargs):
    """
    :param int input_dim:
    :param int output_dim:
    :param int|float num_seqs:
    :param int|dict[str,int] seq_len:
    :param float input_max_value:
    :param float|None input_shift:
    :param float|None input_scale:
    """
    super(DummyDataset, self).__init__(input_dim=input_dim, output_dim=output_dim, num_seqs=num_seqs, **kwargs)
    self.seq_len = seq_len
    self.input_max_value = input_max_value
    if input_shift is None:
      input_shift = -input_max_value / 2.0
    self.input_shift = input_shift
    if input_scale is None:
      input_scale = 1.0 / self.input_max_value
    self.input_scale = input_scale

  def generate_seq(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: DatasetSeq
    """
    seq_len = self.seq_len
    i1 = seq_idx
    i2 = i1 + seq_len * self.num_inputs
    features = numpy.array([((i % self.input_max_value) + self.input_shift) * self.input_scale
                            for i in range(i1, i2)]).reshape((seq_len, self.num_inputs))
    i1, i2 = i2, i2 + seq_len
    targets = numpy.array([i % self.num_outputs["classes"][0]
                           for i in range(i1, i2)])
    return DatasetSeq(seq_idx=seq_idx, features=features, targets=targets)


class DummyDatasetMultipleSequenceLength(DummyDataset):
  """
  Like :class:`DummyDataset` but has provides seqs with different sequence lengths.
  """

  def __init__(self, input_dim, output_dim, num_seqs, seq_len=None,
               input_max_value=10.0, input_shift=None, input_scale=None, **kwargs):
    """
    :param int input_dim:
    :param int output_dim:
    :param int|float num_seqs:
    :param int|dict[str,int] seq_len:
    :param float input_max_value:
    :param float|None input_shift:
    :param float|None input_scale:
    """
    if seq_len is None:
      seq_len = {'data': 10, 'classes': 20}
    super(DummyDatasetMultipleSequenceLength, self).__init__(
      input_dim=input_dim,
      output_dim=output_dim,
      num_seqs=num_seqs,
      seq_len=seq_len,
      input_max_value=input_max_value,
      input_shift=input_shift,
      input_scale=input_scale,
      **kwargs)

  def generate_seq(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: DatasetSeq
    """
    assert isinstance(self.seq_len, dict)
    seq_len_data = self.seq_len['data']
    seq_len_classes = self.seq_len['classes']
    i1 = seq_idx
    i2 = i1 + seq_len_data * self.num_inputs
    features = numpy.array([((i % self.input_max_value) + self.input_shift) * self.input_scale
                            for i in range(i1, i2)]).reshape((seq_len_data, self.num_inputs))
    i1, i2 = i2, i2 + seq_len_classes
    targets = numpy.array([i % self.num_outputs["classes"][0]
                           for i in range(i1, i2)])
    return DatasetSeq(seq_idx=seq_idx, features=features, targets=targets)


class StaticDataset(GeneratingDataset):
  """
  Provide all the data as a list of dict of numpy arrays.
  """

  @classmethod
  def copy_from_dataset(cls, dataset, start_seq_idx=0, max_seqs=None):
    """
    :param Dataset dataset:
    :param int start_seq_idx:
    :param int|None max_seqs:
    :rtype: StaticDataset
    """
    if isinstance(dataset, StaticDataset):
      return cls(
        data=dataset.data, target_list=dataset.target_list,
        output_dim=dataset.num_outputs, input_dim=dataset.num_inputs)
    seq_idx = start_seq_idx
    data = []
    while dataset.is_less_than_num_seqs(seq_idx):
      dataset.load_seqs(seq_idx, seq_idx + 1)
      if max_seqs is not None and len(data) >= max_seqs:
        break
      seq_data = {key: dataset.get_data(seq_idx, key) for key in dataset.get_data_keys()}
      data.append(seq_data)
      seq_idx += 1
    return cls(
      data=data, target_list=dataset.get_target_list(),
      output_dim=dataset.num_outputs, input_dim=dataset.num_inputs)

  def __init__(self, data, target_list=None, output_dim=None, input_dim=None, **kwargs):
    """
    :param list[dict[str,numpy.ndarray]] data: list of seqs, each provide the data for each data-key
    :param int|None input_dim:
    :param int|dict[str,(int,int)|list[int]] output_dim:
    """
    assert len(data) > 0
    self.data = data
    num_seqs = len(data)
    first_data = data[0]
    self.data_keys = sorted(first_data.keys())
    if target_list is not None:
      for key in target_list:
        assert key in self.data_keys
    else:
      target_list = list(self.data_keys)
      if "data" in target_list:
        target_list.remove("data")
    self.target_list = target_list

    if output_dim is None:
      output_dim = {}
    output_dim = convert_data_dims(output_dim, leave_dict_as_is=False)
    if input_dim is not None and "data" not in output_dim:
      assert "data" in self.data_keys
      output_dim["data"] = (input_dim, 2)  # assume dense, not sparse
    for key, value in first_data.items():
      if key not in output_dim:
        output_dim[key] = (value.shape[-1] if value.ndim >= 2 else 0, len(value.shape))
    if input_dim is None and "data" in self.data_keys:
      input_dim = output_dim["data"][0]
    for key in self.data_keys:
      first_data_output = first_data[key]
      assert key in output_dim
      assert output_dim[key][1] == len(first_data_output.shape)
      if len(first_data_output.shape) >= 2:
        assert output_dim[key][0] == first_data_output.shape[-1]
    assert sorted(output_dim.keys()) == self.data_keys, "output_dim does noth match the given data"

    super(StaticDataset, self).__init__(input_dim=input_dim, output_dim=output_dim, num_seqs=num_seqs, **kwargs)

  def generate_seq(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: DatasetSeq
    """
    data = self.data[seq_idx]
    return DatasetSeq(seq_idx=seq_idx, features={key: data[key] for key in self.data_keys})

  def get_data_keys(self):
    """
    :rtype: list[str]
    """
    return self.data_keys

  def get_target_list(self):
    """
    :rtype: list[str]
    """
    return self.target_list

  def get_data_dtype(self, key):
    """
    :param str key:
    :rtype: str
    """
    return self.data[0][key].dtype


class CopyTaskDataset(GeneratingDataset):
  """
  Copy task.
  Input/output is exactly the same random sequence of sparse labels.
  """

  def __init__(self, nsymbols, minlen=0, maxlen=0, minlen_epoch_factor=0, maxlen_epoch_factor=0, **kwargs):
    """
    :param int nsymbols:
    :param int minlen:
    :param int maxlen:
    :param float minlen_epoch_factor:
    :param float maxlen_epoch_factor:
    """
    # Sparse data.
    super(CopyTaskDataset, self).__init__(input_dim=nsymbols,
                                          output_dim={"data": (nsymbols, 1),
                                                      "classes": (nsymbols, 1)},
                                          **kwargs)

    assert nsymbols <= 256
    self.nsymbols = nsymbols
    self.minlen = minlen
    self.maxlen = maxlen
    self.minlen_epoch_factor = minlen_epoch_factor
    self.maxlen_epoch_factor = maxlen_epoch_factor

  def get_random_seq_len(self):
    """
    :rtype: int
    """
    assert isinstance(self.epoch, int)
    minlen = int(self.minlen + self.minlen_epoch_factor * self.epoch)
    maxlen = int(self.maxlen + self.maxlen_epoch_factor * self.epoch)
    assert 0 < minlen <= maxlen
    return self.random.randint(minlen, maxlen + 1)

  def generate_seq(self, seq_idx):
    """
    :type seq_idx: int
    :rtype: DatasetSeq
    """
    seq_len = self.get_random_seq_len()
    seq = [self.random.randint(0, self.nsymbols) for _ in range(seq_len)]
    seq_np = numpy.array(seq, dtype="int8")
    return DatasetSeq(seq_idx=seq_idx, features=seq_np, targets={"classes": seq_np})


# Multiple external sources where we could write automatic wrappers:
# * https://github.com/tensorflow/datasets
# * tf.contrib.keras.datasets, https://www.tensorflow.org/api_docs/python/tf/keras/datasets
# * nltk.corpus


class ExtractAudioFeatures:
  """
  Currently uses librosa to extract MFCC/log-mel features.
  (Alternatives: python_speech_features, talkbox.features.mfcc, librosa)
  """

  def __init__(self,
               window_len=0.025, step_len=0.010,
               num_feature_filters=None, with_delta=False, norm_mean=None, norm_std_dev=None,
               features="mfcc", feature_options=None, random_permute=None, random_state=None, raw_ogg_opts=None,
               pre_process=None, post_process=None,
               sample_rate=None,
               peak_normalization=True, preemphasis=None, join_frames=None):
    """
    :param float window_len: in seconds
    :param float step_len: in seconds
    :param int num_feature_filters:
    :param bool|int with_delta:
    :param numpy.ndarray|str|int|float|None norm_mean: if str, will interpret as filename
    :param numpy.ndarray|str|int|float|None norm_std_dev: if str, will interpret as filename
    :param str|function features: "mfcc", "log_mel_filterbank", "log_log_mel_filterbank", "raw", "raw_ogg"
    :param dict[str]|None feature_options: provide additional parameters for the feature function
    :param CollectionReadCheckCovered|dict[str]|bool|None random_permute:
    :param numpy.random.RandomState|None random_state:
    :param dict[str]|None raw_ogg_opts:
    :param function|None pre_process:
    :param function|None post_process:
    :param int|None sample_rate:
    :param bool peak_normalization: set to False to disable the peak normalization for audio files
    :param float|None preemphasis: set a preemphasis filter coefficient
    :param int|None join_frames: concatenate multiple frames together to a superframe
    :return: (audio_len // int(step_len * sample_rate), (with_delta + 1) * num_feature_filters), float32
    :rtype: numpy.ndarray
    """
    self.window_len = window_len
    self.step_len = step_len
    if num_feature_filters is None:
      if features == "raw":
        num_feature_filters = 1
      elif features == "raw_ogg":
        raise Exception("you should explicitly specify num_feature_filters (dimension) for raw_ogg")
      else:
        num_feature_filters = 40  # was the old default
    self.num_feature_filters = num_feature_filters
    self.preemphasis = preemphasis
    if isinstance(with_delta, bool):
      with_delta = 1 if with_delta else 0
    assert isinstance(with_delta, int) and with_delta >= 0
    self.with_delta = with_delta
    # join frames needs to be set before norm loading
    self.join_frames = join_frames
    if norm_mean is not None:
      if not isinstance(norm_mean, (int, float)):
        norm_mean = self._load_feature_vec(norm_mean)
    if norm_std_dev is not None:
      if not isinstance(norm_std_dev, (int, float)):
        norm_std_dev = self._load_feature_vec(norm_std_dev)
    self.norm_mean = norm_mean
    self.norm_std_dev = norm_std_dev
    if random_permute and not isinstance(random_permute, CollectionReadCheckCovered):
      random_permute = CollectionReadCheckCovered.from_bool_or_dict(random_permute)
    self.random_permute_opts = random_permute
    self.random_state = random_state
    self.features = features
    self.feature_options = feature_options
    self.pre_process = pre_process
    self.post_process = post_process
    self.sample_rate = sample_rate
    self.raw_ogg_opts = raw_ogg_opts
    self.peak_normalization = peak_normalization

  def _load_feature_vec(self, value):
    """
    :param str|None value:
    :return: shape (self.num_inputs,), float32
    :rtype: numpy.ndarray|None
    """
    if value is None:
      return None
    if isinstance(value, str):
      value = numpy.loadtxt(value)
    assert isinstance(value, numpy.ndarray)
    assert value.shape == (self.get_feature_dimension(),)
    return value.astype("float32")

  def get_audio_features_from_raw_bytes(self, raw_bytes, seq_name=None):
    """
    :param io.BytesIO raw_bytes:
    :param str|None seq_name:
    :return: shape (time,feature_dim)
    :rtype: numpy.ndarray
    """
    if self.features == "raw_ogg":
      assert self.with_delta == 0 and self.norm_mean is None and self.norm_std_dev is None
      # We expect that raw_bytes comes from a Ogg file.
      try:
        from extern.ParseOggVorbis.returnn_import import ParseOggVorbisLib
      except ImportError:
        print("Maybe you did not clone the submodule extern/ParseOggVorbis?")
        raise
      return ParseOggVorbisLib.get_instance().get_features_from_raw_bytes(
        raw_bytes=raw_bytes.getvalue(), output_dim=self.num_feature_filters, **(self.raw_ogg_opts or {}))

    # Don't use librosa.load which internally uses audioread which would use Gstreamer as a backend,
    # which has multiple issues:
    # https://github.com/beetbox/audioread/issues/62
    # https://github.com/beetbox/audioread/issues/63
    # Instead, use PySoundFile, which is also faster. See here for discussions:
    # https://github.com/beetbox/audioread/issues/64
    # https://github.com/librosa/librosa/issues/681
    # noinspection PyPackageRequirements
    import soundfile  # pip install pysoundfile
    # integer audio formats are automatically transformed in the range [-1,1]
    audio, sample_rate = soundfile.read(raw_bytes)
    return self.get_audio_features(audio=audio, sample_rate=sample_rate, seq_name=seq_name)

  def get_audio_features(self, audio, sample_rate, seq_name=None):
    """
    :param numpy.ndarray audio: raw audio samples, shape (audio_len,)
    :param int sample_rate: e.g. 22050
    :param str|None seq_name:
    :return: array (time,dim), dim == self.get_feature_dimension()
    :rtype: numpy.ndarray
    """
    if self.sample_rate is not None:
      assert sample_rate == self.sample_rate, "currently no conversion implemented..."

    if self.preemphasis:
      from scipy import signal
      audio = signal.lfilter([1, -self.preemphasis], [1], audio)

    if self.peak_normalization:
      peak = numpy.max(numpy.abs(audio))
      audio /= peak

    if self.random_permute_opts and self.random_permute_opts.truth_value:
      audio = _get_random_permuted_audio(
        audio=audio,
        sample_rate=sample_rate,
        opts=self.random_permute_opts,
        random_state=self.random_state)

    if self.pre_process:
      audio = self.pre_process(audio=audio, sample_rate=sample_rate, random_state=self.random_state)
      assert isinstance(audio, numpy.ndarray) and len(audio.shape) == 1

    if self.features == "raw":
      assert self.num_feature_filters == 1
      feature_data = audio[:, None].astype("float32")  # add dummy dimension

    else:
      kwargs = {
        "sample_rate": sample_rate,
        "window_len": self.window_len,
        "step_len": self.step_len,
        "num_feature_filters": self.num_feature_filters,
        "audio": audio}

      if self.feature_options is not None:
        assert isinstance(self.feature_options, dict)
        kwargs.update(self.feature_options)

      if callable(self.features):
        feature_data = self.features(random_state=self.random_state, **kwargs)
      elif self.features == "mfcc":
        feature_data = _get_audio_features_mfcc(**kwargs)
      elif self.features == "log_mel_filterbank":
        feature_data = _get_audio_log_mel_filterbank(**kwargs)
      elif self.features == "log_log_mel_filterbank":
        feature_data = _get_audio_log_log_mel_filterbank(**kwargs)
      elif self.features == "db_mel_filterbank":
        feature_data = _get_audio_db_mel_filterbank(**kwargs)
      elif self.features == "linear_spectrogram":
        feature_data = _get_audio_linear_spectrogram(**kwargs)
      else:
        raise Exception("non-supported feature type %r" % (self.features,))

    assert feature_data.ndim == 2
    assert feature_data.shape[1] == self.num_feature_filters

    if self.with_delta:
      # noinspection PyPackageRequirements
      import librosa
      deltas = [librosa.feature.delta(feature_data, order=i, axis=0).astype("float32")
                for i in range(1, self.with_delta + 1)]
      feature_data = numpy.concatenate([feature_data] + deltas, axis=1)
      assert feature_data.shape[1] == (self.with_delta + 1) * self.num_feature_filters

    if self.norm_mean is not None:
      if isinstance(self.norm_mean, (int, float)):
        feature_data -= self.norm_mean
      else:
        feature_data -= self.norm_mean[None, :]

    if self.norm_std_dev is not None:
      if isinstance(self.norm_std_dev, (int, float)):
        feature_data /= self.norm_std_dev
      else:
        feature_data /= self.norm_std_dev[None, :]

    if self.join_frames is not None:
      pad_len = self.join_frames - (feature_data.shape[0] % self.join_frames)
      pad_len = pad_len % self.join_frames
      new_len = feature_data.shape[0] + pad_len
      feature_data = numpy.pad(feature_data, pad_width=((0, pad_len), (0, 0)), mode="edge")
      feature_data = numpy.reshape(feature_data,
                                   newshape=(new_len // self.join_frames, feature_data.shape[1] * self.join_frames),
                                   order='C')

    assert feature_data.shape[1] == self.get_feature_dimension()
    if self.post_process:
      feature_data = self.post_process(feature_data, seq_name=seq_name)
      assert isinstance(feature_data, numpy.ndarray) and len(feature_data.shape) == 2
      assert feature_data.shape[1] == self.get_feature_dimension()
    return feature_data

  def get_feature_dimension(self):
    """
    :rtype: int
    """
    return (self.with_delta + 1) * self.num_feature_filters * (self.join_frames or 1)


def _get_audio_linear_spectrogram(audio, sample_rate, window_len=0.025, step_len=0.010, num_feature_filters=512):
  """
  Computes linear spectrogram features from an audio signal.
  Drops the DC component.

  :param numpy.ndarray audio: raw audio samples, shape (audio_len,)
  :param int sample_rate: e.g. 22050
  :param float window_len: in seconds
  :param float step_len: in seconds
  :return: (audio_len // int(step_len * sample_rate), num_feature_filters), float32
  :rtype: numpy.ndarray
  """
  # noinspection PyPackageRequirements
  import librosa

  min_n_fft = int(window_len * sample_rate)
  assert num_feature_filters*2 >= min_n_fft
  assert num_feature_filters % 2 == 0

  spectrogram = numpy.abs(librosa.core.stft(
    audio, hop_length=int(step_len * sample_rate), win_length=int(window_len * sample_rate), n_fft=num_feature_filters*2))

  # remove the DC part
  spectrogram = spectrogram[1:]

  assert spectrogram.shape[0] == num_feature_filters
  spectrogram = spectrogram.transpose().astype("float32")  # (time, dim)
  return spectrogram


def _get_audio_features_mfcc(audio, sample_rate, window_len=0.025, step_len=0.010, num_feature_filters=40):
  """
  :param numpy.ndarray audio: raw audio samples, shape (audio_len,)
  :param int sample_rate: e.g. 22050
  :param float window_len: in seconds
  :param float step_len: in seconds
  :param int num_feature_filters:
  :return: (audio_len // int(step_len * sample_rate), num_feature_filters), float32
  :rtype: numpy.ndarray
  """
  # noinspection PyPackageRequirements
  import librosa
  mfccs = librosa.feature.mfcc(
    audio, sr=sample_rate,
    n_mfcc=num_feature_filters,
    hop_length=int(step_len * sample_rate), n_fft=int(window_len * sample_rate))
  librosa_version = librosa.__version__.split(".")
  if int(librosa_version[0]) >= 1 or (int(librosa_version[0]) == 0 and int(librosa_version[1]) >= 7):
    rms_func = librosa.feature.rms
  else:
    rms_func = librosa.feature.rmse
  energy = rms_func(
    audio,
    hop_length=int(step_len * sample_rate), frame_length=int(window_len * sample_rate))
  mfccs[0] = energy  # replace first MFCC with energy, per convention
  assert mfccs.shape[0] == num_feature_filters  # (dim, time)
  mfccs = mfccs.transpose().astype("float32")  # (time, dim)
  return mfccs


def _get_audio_log_mel_filterbank(audio, sample_rate, window_len=0.025, step_len=0.010, num_feature_filters=80):
  """
  Computes log Mel-filterbank features from an audio signal.
  References:

    https://github.com/jameslyons/python_speech_features/blob/master/python_speech_features/base.py
    https://github.com/tensorflow/tensor2tensor/blob/master/tensor2tensor/data_generators/speech_recognition.py

  :param numpy.ndarray audio: raw audio samples, shape (audio_len,)
  :param int sample_rate: e.g. 22050
  :param float window_len: in seconds
  :param float step_len: in seconds
  :param int num_feature_filters:
  :return: (audio_len // int(step_len * sample_rate), num_feature_filters), float32
  :rtype: numpy.ndarray
  """
  # noinspection PyPackageRequirements
  import librosa
  mel_filterbank = librosa.feature.melspectrogram(
    audio, sr=sample_rate,
    n_mels=num_feature_filters,
    hop_length=int(step_len * sample_rate), n_fft=int(window_len * sample_rate))
  log_noise_floor = 1e-3  # prevent numeric overflow in log
  log_mel_filterbank = numpy.log(numpy.maximum(log_noise_floor, mel_filterbank))
  assert log_mel_filterbank.shape[0] == num_feature_filters
  log_mel_filterbank = log_mel_filterbank.transpose().astype("float32")  # (time, dim)
  return log_mel_filterbank


def _get_audio_db_mel_filterbank(audio, sample_rate,
                                 window_len=0.025, step_len=0.010, num_feature_filters=80, fmin=0, min_amp=1e-10):
  """
  Computes log Mel-filterbank features in dezibel values from an audio signal.
  Provides adjustable minimum frequency and minimual amplitude clipping

  :param numpy.ndarray audio: raw audio samples, shape (audio_len,)
  :param int sample_rate: e.g. 22050
  :param float window_len: in seconds
  :param float step_len: in seconds
  :param int num_feature_filters: number of mel-filterbanks
  :param int fmin: minimum frequency covered by mel filters
  :param int min_amp: silence clipping for small amplitudes
  :return: (audio_len // int(step_len * sample_rate), num_feature_filters), float32
  :rtype: numpy.ndarray
  """
  # noinspection PyPackageRequirements
  assert fmin >= 0
  assert min_amp > 0

  import librosa
  mel_filterbank = librosa.feature.melspectrogram(
    audio, sr=sample_rate,
    n_mels=num_feature_filters,
    hop_length=int(step_len * sample_rate),
    n_fft=int(window_len * sample_rate),
    fmin=fmin
   )

  log_mel_filterbank = 20 * numpy.log10(numpy.maximum(min_amp, mel_filterbank))
  assert log_mel_filterbank.shape[0] == num_feature_filters
  log_mel_filterbank = log_mel_filterbank.transpose().astype("float32")  # (time, dim)
  return log_mel_filterbank


def _get_audio_log_log_mel_filterbank(audio, sample_rate, window_len=0.025, step_len=0.010, num_feature_filters=80):
  """
  Computes log-log Mel-filterbank features from an audio signal.
  References:

    https://github.com/jameslyons/python_speech_features/blob/master/python_speech_features/base.py
    https://github.com/tensorflow/tensor2tensor/blob/master/tensor2tensor/data_generators/speech_recognition.py

  :param numpy.ndarray audio: raw audio samples, shape (audio_len,)
  :param int sample_rate: e.g. 22050
  :param float window_len: in seconds
  :param float step_len: in seconds
  :param int num_feature_filters:
  :return: (audio_len // int(step_len * sample_rate), num_feature_filters), float32
  :rtype: numpy.ndarray
  """
  # noinspection PyPackageRequirements
  import librosa
  mel_filterbank = librosa.feature.melspectrogram(
    audio, sr=sample_rate,
    n_mels=num_feature_filters,
    hop_length=int(step_len * sample_rate), n_fft=int(window_len * sample_rate))
  log_noise_floor = 1e-3  # prevent numeric overflow in log
  log_mel_filterbank = numpy.log(numpy.maximum(log_noise_floor, mel_filterbank))
  log_log_mel_filterbank = librosa.core.amplitude_to_db(log_mel_filterbank)
  assert log_log_mel_filterbank.shape[0] == num_feature_filters
  log_log_mel_filterbank = log_log_mel_filterbank.transpose().astype("float32")  # (time, dim)
  return log_log_mel_filterbank


def _get_random_permuted_audio(audio, sample_rate, opts, random_state):
  """
  :param numpy.ndarray audio: raw time signal
  :param int sample_rate:
  :param CollectionReadCheckCovered opts:
  :param numpy.random.RandomState random_state:
  :return: audio randomly permuted
  :rtype: numpy.ndarray
  """
  # noinspection PyPackageRequirements
  import librosa
  # noinspection PyPackageRequirements
  import scipy.ndimage
  import warnings
  audio = audio * random_state.uniform(opts.get("rnd_scale_lower", 0.8), opts.get("rnd_scale_upper", 1.0))
  if random_state.uniform(0.0, 1.0) < opts.get("rnd_zoom_switch", 0.2):
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      # Alternative: scipy.interpolate.interp2d
      factor = random_state.uniform(opts.get("rnd_zoom_lower", 0.9), opts.get("rnd_zoom_upper", 1.1))
      audio = scipy.ndimage.zoom(audio, factor, order=3)
  if random_state.uniform(0.0, 1.0) < opts.get("rnd_stretch_switch", 0.2):
    rate = random_state.uniform(opts.get("rnd_stretch_lower", 0.9), opts.get("rnd_stretch_upper", 1.2))
    audio = librosa.effects.time_stretch(audio, rate=rate)
  if random_state.uniform(0.0, 1.0) < opts.get("rnd_pitch_switch", 0.2):
    n_steps = random_state.uniform(opts.get("rnd_pitch_lower", -1.), opts.get("rnd_pitch_upper", 1.))
    audio = librosa.effects.pitch_shift(audio, sr=sample_rate, n_steps=n_steps)
  opts.assert_all_read()
  return audio


class TimitDataset(CachedDataset2):
  """
  DARPA TIMIT Acoustic-Phonetic Continuous Speech Corpus.
  You must provide the data.

  Demo:

      tools/dump-dataset.py "{'class': 'TimitDataset', 'timit_dir': '...'}"
      tools/dump-dataset.py "{'class': 'TimitDataset', 'timit_dir': '...',
                              'demo_play_audio': True, 'random_permute_audio': True}"

  The full train data has 3696 utterances and the core test data has 192 utterances
  (24-speaker core test set).

  For some references:
  https://github.com/ppwwyyxx/tensorpack/blob/master/examples/CTC-TIMIT/train-timit.py
  https://www.cs.toronto.edu/~graves/preprint.pdf
  https://arxiv.org/pdf/1303.5778.pdf
  https://arxiv.org/pdf/0804.3269.pdf
  """

  # via: https://github.com/kaldi-asr/kaldi/blob/master/egs/timit/s5/conf/phones.60-48-39.map
  PhoneMapTo39 = {
    'aa': 'aa', 'ae': 'ae', 'ah': 'ah', 'ao': 'aa', 'aw': 'aw', 'ax': 'ah', 'ax-h': 'ah', 'axr': 'er',
    'ay': 'ay', 'b': 'b', 'bcl': 'sil', 'ch': 'ch', 'd': 'd', 'dcl': 'sil', 'dh': 'dh', 'dx': 'dx', 'eh': 'eh',
    'el': 'l', 'em': 'm', 'en': 'n', 'eng': 'ng', 'epi': 'sil', 'er': 'er', 'ey': 'ey', 'f': 'f', 'g': 'g',
    'gcl': 'sil', 'h#': 'sil', 'hh': 'hh', 'hv': 'hh', 'ih': 'ih', 'ix': 'ih', 'iy': 'iy', 'jh': 'jh',
    'k': 'k', 'kcl': 'sil', 'l': 'l', 'm': 'm', 'n': 'n', 'ng': 'ng', 'nx': 'n', 'ow': 'ow', 'oy': 'oy',
    'p': 'p', 'pau': 'sil', 'pcl': 'sil', 'q': None, 'r': 'r', 's': 's', 'sh': 'sh', 't': 't', 'tcl': 'sil',
    'th': 'th', 'uh': 'uh', 'uw': 'uw', 'ux': 'uw', 'v': 'v', 'w': 'w', 'y': 'y', 'z': 'z', 'zh': 'sh'}
  PhoneMapTo48 = {
    'aa': 'aa', 'ae': 'ae', 'ah': 'ah', 'ao': 'ao', 'aw': 'aw', 'ax': 'ax', 'ax-h': 'ax', 'axr': 'er',
    'ay': 'ay', 'b': 'b', 'bcl': 'vcl', 'ch': 'ch', 'd': 'd', 'dcl': 'vcl', 'dh': 'dh', 'dx': 'dx', 'eh': 'eh',
    'el': 'el', 'em': 'm', 'en': 'en', 'eng': 'ng', 'epi': 'epi', 'er': 'er', 'ey': 'ey', 'f': 'f', 'g': 'g',
    'gcl': 'vcl', 'h#': 'sil', 'hh': 'hh', 'hv': 'hh', 'ih': 'ih', 'ix': 'ix', 'iy': 'iy', 'jh': 'jh',
    'k': 'k', 'kcl': 'cl', 'l': 'l', 'm': 'm', 'n': 'n', 'ng': 'ng', 'nx': 'n', 'ow': 'ow', 'oy': 'oy',
    'p': 'p', 'pau': 'sil', 'pcl': 'cl', 'q': None, 'r': 'r', 's': 's', 'sh': 'sh', 't': 't', 'tcl': 'cl',
    'th': 'th', 'uh': 'uh', 'uw': 'uw', 'ux': 'uw', 'v': 'v', 'w': 'w', 'y': 'y', 'z': 'z', 'zh': 'zh'}
  Phones61 = PhoneMapTo39.keys()
  PhoneMapTo61 = {p: p for p in Phones61}

  @classmethod
  def _get_phone_map(cls, num_phones=61):
    """
    :param int num_phones:
    :return: map 61-phone-set-phone -> num_phones-phone-set-phone
    :rtype: dict[str,str|None]
    """
    return {61: cls.PhoneMapTo61, 48: cls.PhoneMapTo48, 39: cls.PhoneMapTo39}[num_phones]

  @classmethod
  def _get_labels(cls, phone_map):
    """
    :param dict[str,str|None] phone_map:
    :rtype: list[str]
    """
    labels = sorted(set(filter(None, phone_map.values())))
    # Make 'sil' the 0 phoneme.
    if "pau" in labels:
      labels.remove("pau")
      labels.insert(0, "pau")
    else:
      labels.remove("sil")
      labels.insert(0, "sil")
    return labels

  @classmethod
  def get_label_map(cls, source_num_phones=61, target_num_phones=39):
    """
    :param int source_num_phones:
    :param int target_num_phones:
    :rtype: dict[int,int|None]
    """
    src_phone_map = cls._get_phone_map(source_num_phones)  # 61-phone -> src-phone
    src_labels = cls._get_labels(src_phone_map)  # src-idx -> src-phone
    tgt_phone_map = cls._get_phone_map(target_num_phones)  # 61-phone -> tgt-phone
    tgt_labels = cls._get_labels(tgt_phone_map)  # tgt-idx -> tgt-phone
    d = {i: src_labels[i] for i in range(source_num_phones)}  # src-idx -> src-phone|61-phone
    if source_num_phones != 61:
      src_phone_map_rev = {v: k for (k, v) in sorted(src_phone_map.items())}  # src-phone -> 61-phone
      d = {i: src_phone_map_rev[v] for (i, v) in d.items()}  # src-idx -> 61-phone
    d = {i: tgt_phone_map[v] for (i, v) in d.items()}  # src-idx -> tgt-phone
    d = {i: tgt_labels.index(v) if v else None for (i, v) in d.items()}  # src-idx -> tgt-idx
    return d

  def __init__(self, timit_dir, train=True, preload=False,
               num_feature_filters=40, feature_window_len=0.025, feature_step_len=0.010, with_delta=False,
               norm_mean=None, norm_std_dev=None,
               random_permute_audio=None, num_phones=61,
               demo_play_audio=False, fixed_random_seed=None, **kwargs):
    """
    :param str|None timit_dir: directory of TIMIT. should contain train/filelist.phn and test/filelist.core.phn
    :param bool train: whether to use the train or core test data
    :param bool preload: if True, here at __init__, we will wait until we loaded all the data
    :param int num_feature_filters: e.g. number of MFCCs
    :param bool|int with_delta: whether to add delta features (doubles the features dim). if int, up to this degree
    :param str norm_mean: file with mean values which are used for mean-normalization of the final features
    :param str norm_std_dev: file with std dev valeus for variance-normalization of the final features
    :param None|bool|dict[str] random_permute_audio: enables permutation on the audio. see _get_random_permuted_audio
    :param int num_phones: 39, 48 or 61. num labels of our classes
    :param bool demo_play_audio: plays the audio. only make sense with tools/dump-dataset.py
    :param None|int fixed_random_seed: if given, use this fixed random seed in every epoch
    """
    super(TimitDataset, self).__init__(**kwargs)
    from threading import Lock, Thread
    self._lock = Lock()
    self._num_feature_filters = num_feature_filters
    self._feature_window_len = feature_window_len
    self._feature_step_len = feature_step_len
    self.num_inputs = self._num_feature_filters
    if isinstance(with_delta, bool):
      with_delta = 1 if with_delta else 0
    assert isinstance(with_delta, int) and with_delta >= 0
    self._with_delta = with_delta
    self.num_inputs *= (1 + with_delta)
    self._norm_mean = self._load_feature_vec(norm_mean)
    self._norm_std_dev = self._load_feature_vec(norm_std_dev)
    assert num_phones in {61, 48, 39}
    self._phone_map = {61: self.PhoneMapTo61, 48: self.PhoneMapTo48, 39: self.PhoneMapTo39}[num_phones]
    self.labels = self._get_labels(self._phone_map)
    self.num_outputs = {"data": (self.num_inputs, 2), "classes": (len(self.labels), 1)}
    self._timit_dir = timit_dir
    self._is_train = train
    self._demo_play_audio = demo_play_audio
    self._random = numpy.random.RandomState(1)
    self._fixed_random_seed = fixed_random_seed  # useful when used as eval dataset
    if random_permute_audio is None:
      random_permute_audio = train
    from Util import CollectionReadCheckCovered
    self._random_permute_audio = CollectionReadCheckCovered.from_bool_or_dict(random_permute_audio)

    self._seq_order = None  # type: typing.Optional[typing.List[int]]
    self._init_timit()

    self._audio_data = {}  # seq_tag -> (audio, sample_rate). loaded by self._reader_thread_main
    self._phone_seqs = {}  # seq_tag -> phone_seq (list of str)
    self._reader_thread = Thread(name="%r reader" % self, target=self._reader_thread_main)
    self._reader_thread.daemon = True
    self._reader_thread.start()
    if preload:
      self._preload()

  def _load_feature_vec(self, value):
    """
    :param str|None value:
    :return: shape (self.num_inputs,), float32
    :rtype: numpy.ndarray|None
    """
    if value is None:
      return None
    if isinstance(value, str):
      value = numpy.loadtxt(value)
    assert isinstance(value, numpy.ndarray)
    assert value.shape == (self.num_inputs,)
    return value.astype("float32")

  def _init_timit(self):
    """
    Sets self._seq_tags, _num_seqs, _seq_order, and _timit_dir.
    timit_dir should be such that audio_filename = "%s/%s.wav" % (timit_dir, seq_tag).
    """
    import os
    assert os.path.exists(self._timit_dir)
    if self._is_train:
      self._timit_dir += "/train"
    else:
      self._timit_dir += "/test"
    assert os.path.exists(self._timit_dir)
    if self._is_train:
      file_list_fn = self._timit_dir + "/filelist.phn"
    else:
      file_list_fn = self._timit_dir + "/filelist.core.phn"
    assert os.path.exists(file_list_fn)
    seq_tags = [os.path.splitext(p)[0] for p in open(file_list_fn).read().splitlines()]
    self._seq_tags = seq_tags
    self._num_seqs = len(self._seq_tags)
    self._seq_order = list(range(self._num_seqs))

  def _preload(self):
    import time
    last_print_time = 0
    last_print_len = None
    while True:
      with self._lock:
        cur_len = len(self._audio_data)
      if cur_len == len(self._seq_tags):
        return
      if cur_len != last_print_len and time.time() - last_print_time > 10:
        print("%r: loading (%i/%i loaded so far)..." % (
          self, cur_len, len(self._seq_tags)), file=log.v3)
        last_print_len = cur_len
        last_print_time = time.time()
      time.sleep(1)

  def _reader_thread_main(self):
    import sys
    from Util import interrupt_main
    # noinspection PyBroadException
    try:
      import better_exchook
      better_exchook.install()

      # noinspection PyPackageRequirements
      import librosa

      for seq_tag in self._seq_tags:
        audio_filename = "%s/%s.wav" % (self._timit_dir, seq_tag)
        # Don't use librosa.load which internally uses audioread which would use Gstreamer as a backend,
        # which has multiple issues:
        # https://github.com/beetbox/audioread/issues/62
        # https://github.com/beetbox/audioread/issues/63
        # Instead, use PySoundFile, which is also faster. See here for discussions:
        # https://github.com/beetbox/audioread/issues/64
        # https://github.com/librosa/librosa/issues/681
        # noinspection PyPackageRequirements
        import soundfile  # pip install pysoundfile
        audio, sample_rate = soundfile.read(audio_filename)
        with self._lock:
          self._audio_data[seq_tag] = (audio, sample_rate)
        phone_seq = self._read_phone_seq(seq_tag)
        with self._lock:
          self._phone_seqs[seq_tag] = phone_seq

    except Exception:
      sys.excepthook(*sys.exc_info())
      interrupt_main()

  def _read_phn_file(self, seq_tag):
    """
    :param str seq_tag:
    :rtype: list[str]
    """
    import os
    phn_fn = "%s/%s.phn" % (self._timit_dir, seq_tag)
    assert os.path.exists(phn_fn)
    phone_seq = []
    for l in open(phn_fn).read().splitlines():
      t0, t1, p = l.split()
      phone_seq.append(p)
    return phone_seq

  def _read_phone_seq(self, seq_tag):
    """
    :param str seq_tag: e.g. "dr1-fvmh0/s1" or "dr1/fcjf0/sa1"
    :rtype: list[str]
    """
    return self._read_phn_file(seq_tag)

  def _get_phone_seq(self, seq_tag):
    """
    :param str seq_tag: e.g. "dr1-fvmh0/s1" or "dr1/fcjf0/sa1"
    :rtype: list[str]
    """
    import time
    last_print_time = 0
    last_print_len = None
    idx = None
    while True:
      with self._lock:
        if seq_tag in self._phone_seqs:
          return self._phone_seqs[seq_tag]
        cur_len = len(self._phone_seqs)
      if idx is None:
        idx = self._seq_tags.index(seq_tag)
      if cur_len != last_print_len and time.time() - last_print_time > 10:
        print("%r: waiting for %r, idx %i (%i/%i loaded so far)..." % (
          self, seq_tag, idx, cur_len, len(self._seq_tags)), file=log.v3)
        last_print_len = cur_len
        last_print_time = time.time()
      time.sleep(1)

  def _get_audio(self, seq_tag):
    """
    :param str seq_tag: e.g. "dr1-fvmh0/s1" or "dr1/fcjf0/sa1"
    :return: audio, sample_rate
    :rtype: (numpy.ndarray, int)
    """
    import time
    last_print_time = 0
    last_print_len = None
    idx = None
    while True:
      with self._lock:
        if seq_tag in self._audio_data:
          return self._audio_data[seq_tag]
        cur_len = len(self._audio_data)
      if idx is None:
        idx = self._seq_tags.index(seq_tag)
      if cur_len != last_print_len and time.time() - last_print_time > 10:
        print("%r: waiting for %r, idx %i (%i/%i loaded so far)..." % (
          self, seq_tag, idx, cur_len, len(self._seq_tags)), file=log.v3)
        last_print_len = cur_len
        last_print_time = time.time()
      time.sleep(1)

  # noinspection PyMethodMayBeStatic
  def _demo_audio_play(self, audio, sample_rate):
    """
    :param numpy.ndarray audio: shape (sample_len,)
    :param int sample_rate:
    """
    assert audio.dtype == numpy.float32
    assert audio.ndim == 1
    try:
      # noinspection PyPackageRequirements
      import pyaudio
    except ImportError:
      print("pip3 install --user pyaudio")
      raise
    p = pyaudio.PyAudio()
    chunk_size = 1024
    stream = p.open(
      format=pyaudio.paFloat32,
      channels=1,
      rate=sample_rate,
      frames_per_buffer=chunk_size,
      output=True)
    while len(audio) > 0:
      chunk = audio[:chunk_size]
      audio = audio[chunk_size:]
      stream.write(chunk, num_frames=len(chunk))
    stream.stop_stream()
    stream.close()
    p.terminate()

  def init_seq_order(self, epoch=None, seq_list=None):
    """
    :param int epoch:
    :param list[str]|None seq_list:
    :rtype: bool
    """
    assert seq_list is None
    super(TimitDataset, self).init_seq_order(epoch=epoch, seq_list=seq_list)
    self._num_seqs = len(self._seq_tags)
    self._seq_order = self.get_seq_order_for_epoch(
      epoch=epoch, num_seqs=self._num_seqs, get_seq_len=lambda i: len(self._seq_tags[i][1]))
    self._random.seed(self._fixed_random_seed or epoch or 1)
    return True

  def _get_random_permuted_audio(self, audio, sample_rate):
    """
    :param numpy.ndarray audio: raw time signal
    :param int sample_rate:
    :return: audio randomly permuted
    :rtype: numpy.ndarray
    """
    return _get_random_permuted_audio(
      audio=audio, sample_rate=sample_rate, opts=self._random_permute_audio, random_state=self._random)

  def _collect_single_seq(self, seq_idx):
    """
    :type seq_idx: int
    :rtype: DatasetSeq | None
    :returns DatasetSeq or None if seq_idx >= num_seqs.
    """
    if seq_idx >= len(self._seq_order):
      return None

    seq_tag = self._seq_tags[self._seq_order[seq_idx]]
    phone_seq = self._get_phone_seq(seq_tag)
    phone_seq = [self._phone_map[p] for p in phone_seq]
    phone_seq = [p for p in phone_seq if p]
    phone_id_seq = numpy.array([self.labels.index(p) for p in phone_seq], dtype="int32")
    # see: https://github.com/rdadolf/fathom/blob/master/fathom/speech/preproc.py
    # and: https://groups.google.com/forum/#!topic/librosa/V4Z1HpTKn8Q
    audio, sample_rate = self._get_audio(seq_tag)
    audio_feature_extractor = ExtractAudioFeatures(
      window_len=self._feature_window_len, step_len=self._feature_step_len,
      num_feature_filters=self._num_feature_filters, with_delta=self._with_delta,
      norm_mean=self._norm_mean, norm_std_dev=self._norm_std_dev,
      random_permute=self._random_permute_audio, random_state=self._random)
    mfccs = audio_feature_extractor.get_audio_features(
      audio=audio, sample_rate=sample_rate, seq_name=seq_tag)
    return DatasetSeq(seq_idx=seq_idx, seq_tag=seq_tag, features=mfccs, targets=phone_id_seq)


class NltkTimitDataset(TimitDataset):
  """
  DARPA TIMIT Acoustic-Phonetic Continuous Speech Corpus

  This Dataset will get TIMIT via NLTK.
  Demo:

      tools/dump-dataset.py "{'class': 'NltkTimitDataset'}"
      tools/dump-dataset.py "{'class': 'NltkTimitDataset', 'demo_play_audio': True, 'random_permute_audio': True}"

  Note: The NLTK data only contains a subset of the train data (160 utterances),
  and none of the test data.
  The full train data has 3696 utterances and the core test data has 192 utterances.
  Not sure how useful this is...
  """

  def __init__(self, nltk_download_dir=None, **kwargs):
    self._nltk_download_dir = nltk_download_dir
    super(NltkTimitDataset, self).__init__(timit_dir=None, **kwargs)

  # noinspection PyPackageRequirements
  def _init_timit(self):
    """
    Sets self._seq_tags, _num_seqs, _seq_order, and _timit_dir.
    timit_dir should be such that audio_filename = "%s/%s.wav" % (timit_dir, seq_tag).
    """
    import os
    from nltk.downloader import Downloader
    downloader = Downloader(download_dir=self._nltk_download_dir)
    print("NLTK corpus download dir:", downloader.download_dir, file=log.v3)
    timit_dir = downloader.download_dir + "/corpora/timit"
    if not os.path.exists(timit_dir):
      assert downloader.download("timit")
      assert os.path.exists(timit_dir)
    assert os.path.exists(timit_dir + "/timitdic.txt"), "TIMIT download broken? remove the directory %r" % timit_dir
    self._timit_dir = timit_dir

    from nltk.data import FileSystemPathPointer
    from nltk.corpus.reader.timit import TimitCorpusReader
    self._data_reader = TimitCorpusReader(FileSystemPathPointer(timit_dir))
    utterance_ids = self._data_reader.utteranceids()
    assert isinstance(utterance_ids, list)
    assert utterance_ids

    # NLTK only has this single set, thus split it into train/dev.
    split = int(len(utterance_ids) * 0.9)
    if self._is_train:
      utterance_ids = utterance_ids[:split]
    else:
      utterance_ids = utterance_ids[split:]
    self._seq_tags = utterance_ids  # list of seq_tag

    self._num_seqs = len(self._seq_tags)
    self._seq_order = list(range(self._num_seqs))

  def _read_phone_seq(self, seq_tag):
    return self._data_reader.phones(seq_tag)


class Vocabulary(object):
  """
  Represents a vocabulary (set of words, and their ids).
  Used by :class:`BytePairEncoding`.
  """

  _cache = {}  # filename -> vocab dict, labels dict (see _parse_vocab)

  @classmethod
  def create_vocab(cls, **opts):
    """
    :param opts: kwargs for class
    :rtype: Vocabulary|BytePairEncoding|CharacterTargets
    """
    opts = opts.copy()
    clz = cls
    if "class" in opts:
      class_name = opts.pop("class")
      clz = globals()[class_name]
      assert issubclass(clz, Vocabulary), "class %r %r is not a subclass of %r" % (class_name, clz, cls)
    elif "bpe_file" in opts:
      clz = BytePairEncoding
    return clz(**opts)

  def __init__(self, vocab_file, seq_postfix=None, unknown_label="UNK", num_labels=None):
    """
    :param str|None vocab_file:
    :param str|None unknown_label:
    :param int num_labels: just for verification
    :param list[int]|None seq_postfix: labels will be added to the seq in self.get_seq
    """
    self.vocab_file = vocab_file
    self.unknown_label = unknown_label
    self.num_labels = None  # will be set by _parse_vocab
    self._parse_vocab()
    if num_labels is not None:
      assert self.num_labels == num_labels
    self.unknown_label_id = self.vocab[self.unknown_label] if self.unknown_label is not None else None
    self.seq_postfix = seq_postfix or []

  def __repr__(self):
    return "Vocabulary(%r, num_labels=%s, unknown_label=%r)" % (self.vocab_file, self.num_labels, self.unknown_label)

  def _parse_vocab(self):
    """
    Sets self.vocab, self.labels, self.num_labels.
    """
    filename = self.vocab_file
    import pickle
    if filename in self._cache:
      self.vocab, self.labels = self._cache[filename]
      assert self.unknown_label is None or self.unknown_label in self.vocab
      self.num_labels = len(self.labels)
    else:
      if filename[-4:] == ".pkl":
        d = pickle.load(open(filename, "rb"))
      else:
        d = eval(open(filename, "r").read())
        if not PY3:
          # Any utf8 string will not be a unicode string automatically, so enforce this.
          assert isinstance(d, dict)
          from Util import py2_utf8_str_to_unicode
          d = {py2_utf8_str_to_unicode(s): i for (s, i) in d.items()}
      assert isinstance(d, dict)
      assert self.unknown_label is None or self.unknown_label in d
      labels = {idx: label for (label, idx) in sorted(d.items())}
      min_label, max_label, num_labels = min(labels), max(labels), len(labels)
      assert 0 == min_label
      if num_labels - 1 < max_label:
        print("Vocab error: not all indices used? max label: %i" % max_label, file=log.v1)
        print("unused labels: %r" % ([i for i in range(max_label + 1) if i not in labels],), file=log.v2)
      assert num_labels - 1 == max_label
      self.num_labels = len(labels)
      self.vocab = d
      self.labels = [label for (idx, label) in sorted(labels.items())]
      self._cache[filename] = (self.vocab, self.labels)

  @classmethod
  def create_vocab_dict_from_labels(cls, labels):
    """
    This is exactly the format which we expect when we read it in self._parse_vocab.

    :param list[str] labels:
    :rtype: dict[str,int]
    """
    d = {label: idx for (idx, label) in enumerate(labels)}
    assert len(d) == len(labels), "some labels are provided multiple times"
    return d

  def tf_get_init_variable_func(self, var):
    """
    :param tensorflow.Variable var:
    :rtype: (tensorflow.Session)->None
    """
    import tensorflow as tf
    from TFUtil import VariableAssigner
    assert isinstance(var, tf.Variable)
    assert var.dtype.base_dtype == tf.string
    assert var.shape.as_list() == [self.num_labels]
    assert len(self.labels) == self.num_labels

    def init_vocab_var(session):
      """
      :param tensorflow.Session session:
      """
      VariableAssigner(var).assign(session=session, value=self.labels)

    return init_vocab_var

  def get_seq(self, sentence):
    """
    :param str sentence: assumed to be seq of vocab entries separated by whitespace
    :rtype: list[int]
    """
    segments = sentence.split()
    return self.get_seq_indices(segments) + self.seq_postfix

  def get_seq_indices(self, seq):
    """
    :param list[str] seq:
    :rtype: list[int]
    """
    if self.unknown_label is not None:
      return [self.vocab.get(k, self.unknown_label_id) for k in seq]
    return [self.vocab[k] for k in seq]

  def get_seq_labels(self, seq):
    """
    :param list[int] seq:
    :rtype: str
    """
    return " ".join(map(self.labels.__getitem__, seq))


class BytePairEncoding(Vocabulary):
  """
  Code is partly taken from subword-nmt/apply_bpe.py.
  Author: Rico Sennrich, code under MIT license.

  Use operations learned with learn_bpe.py to encode a new text.
  The text will not be smaller, but use only a fixed vocabulary, with rare words
  encoded as variable-length sequences of subword units.

  Reference:
  Rico Sennrich, Barry Haddow and Alexandra Birch (2016). Neural Machine Translation of Rare Words with Subword Units.
  Proceedings of the 54th Annual Meeting of the Association for Computational Linguistics (ACL 2016). Berlin, Germany.
  """

  def __init__(self, vocab_file, bpe_file, seq_postfix=None, unknown_label="UNK"):
    """
    :param str vocab_file:
    :param str bpe_file:
    :param list[int]|None seq_postfix: labels will be added to the seq in self.get_seq
    :param str|None unknown_label:
    """
    super(BytePairEncoding, self).__init__(vocab_file=vocab_file, seq_postfix=seq_postfix, unknown_label=unknown_label)
    # check version information
    bpe_file_first_line = open(bpe_file, "r").readline()
    if bpe_file_first_line.startswith('#version:'):
      self._bpe_file_version = tuple(
        [int(x) for x in re.sub(r'(\.0+)*$', '', bpe_file_first_line.split()[-1]).split(".")])
    else:
      self._bpe_file_version = (0, 1)
    self._bpe_codes = [tuple(item.split()) for item in open(bpe_file, "rb").read().decode("utf8").splitlines()]
    # some hacking to deal with duplicates (only consider first instance)
    self._bpe_codes = dict([(code, i) for (i, code) in reversed(list(enumerate(self._bpe_codes)))])
    self._bpe_codes_reverse = dict([(pair[0] + pair[1], pair) for pair, i in self._bpe_codes.items()])
    self._bpe_encode_cache = {}
    self._bpe_separator = '@@'

  @staticmethod
  def _get_pairs(word):
    """
    :param tuple[str] word: represented as tuple of symbols (symbols being variable-length strings)
    :return: set of symbol pairs in a word
    :rtype: set[(str,str)]
    """
    pairs = set()
    prev_char = word[0]
    for char in word[1:]:
      pairs.add((prev_char, char))
      prev_char = char
    return pairs

  def _encode_word(self, orig):
    """
    Encode word based on list of BPE merge operations, which are applied consecutively.
    :param str orig:
    :rtype: tuple[str]
    """

    if orig in self._bpe_encode_cache:
      return self._bpe_encode_cache[orig]

    if self._bpe_file_version == (0, 1):
      word = tuple(orig) + ('</w>',)
    elif self._bpe_file_version == (0, 2):  # more consistent handling of word-final segments
      word = tuple(orig[:-1]) + (orig[-1] + '</w>',)
    else:
      raise NotImplementedError

    pairs = self._get_pairs(word)
    if not pairs:
      return orig

    while True:
      bigram = min(pairs, key=lambda pair: self._bpe_codes.get(pair, float('inf')))
      if bigram not in self._bpe_codes:
        break
      first, second = bigram
      new_word = []
      i = 0
      while i < len(word):
        try:
          j = word.index(first, i)
          new_word.extend(word[i:j])
          i = j
        except ValueError:
          new_word.extend(word[i:])
          break

        if word[i] == first and i < len(word) - 1 and word[i + 1] == second:
          new_word.append(first + second)
          i += 2
        else:
          new_word.append(word[i])
          i += 1
      new_word = tuple(new_word)
      word = new_word
      if len(word) == 1:
        break
      else:
        pairs = self._get_pairs(word)

    # don't print end-of-word symbols
    if word[-1] == '</w>':
      word = word[:-1]
    elif word[-1].endswith('</w>'):
      word = word[:-1] + (word[-1].replace('</w>', ''),)

    if self.labels:
      word = self.check_vocab_and_split(word, self._bpe_codes_reverse, self.labels, self._bpe_separator)

    self._bpe_encode_cache[orig] = word
    return word

  def check_vocab_and_split(self, orig, bpe_codes, vocab, separator):
    """Check for each segment in word if it is in-vocabulary,
    and segment OOV segments into smaller units by reversing the BPE merge operations"""

    out = []

    for segment in orig[:-1]:
      if segment + separator in vocab:
        out.append(segment)
      else:
        # sys.stderr.write('OOV: {0}\n'.format(segment))
        for item in self.recursive_split(segment, bpe_codes, vocab, separator, False):
          out.append(item)

    segment = orig[-1]
    if segment in vocab:
      out.append(segment)
    else:
      # sys.stderr.write('OOV: {0}\n'.format(segment))
      for item in self.recursive_split(segment, bpe_codes, vocab, separator, True):
        out.append(item)

    return out

  def recursive_split(self, segment, bpe_codes, vocab, separator, final=False):
    """Recursively split segment into smaller units (by reversing BPE merges)
    until all units are either in-vocabulary, or cannot be split further."""

    # noinspection PyBroadException
    try:
      if final:
        left, right = bpe_codes[segment + '</w>']
        right = right[:-4]
      else:
        left, right = bpe_codes[segment]
    except Exception:  # TODO fix
      # sys.stderr.write('cannot split {0} further.\n'.format(segment))
      yield segment
      return

    if left + separator in vocab:
      yield left
    else:
      for item in self.recursive_split(left, bpe_codes, vocab, separator, False):
        yield item

    if (final and right in vocab) or (not final and right + separator in vocab):
      yield right
    else:
      for item in self.recursive_split(right, bpe_codes, vocab, separator, final):
        yield item

  def _segment_sentence(self, sentence):
    """
    Segment single sentence (whitespace-tokenized string) with BPE encoding.
    :param str sentence:
    :rtype: list[str]
    """

    output = []

    found_category = False
    skip_category = False

    for word in sentence.split():
      if word[0] == '$' and len(word) > 1:
        found_category = True
        output.append(word)
      elif found_category is True and word[0] == '{':
        skip_category = True
        output.append(word)
      elif skip_category is True and word[0] != '}':
        output.append(word)
      else:
        found_category = False
        skip_category = False
        new_word = self._encode_word(word)

        for item in new_word[:-1]:
          output.append(item + self._bpe_separator)
        output.append(new_word[-1])

    return output

  def get_seq(self, sentence):
    """
    :param str sentence:
    :rtype: list[int]
    """
    segments = self._segment_sentence(sentence)
    seq = self.get_seq_indices(segments)
    return seq + self.seq_postfix


class CharacterTargets(Vocabulary):
  """
  Uses characters as target labels.
  Also see :class:`Utf8ByteTargets`.
  """

  def __init__(self, vocab_file, seq_postfix=None, unknown_label="@"):
    """
    :param str vocab_file:
    :param list[int]|None seq_postfix: labels will be added to the seq in self.get_seq
    :param str|None unknown_label:
    """
    super(CharacterTargets, self).__init__(vocab_file=vocab_file, seq_postfix=seq_postfix, unknown_label=unknown_label)

  def get_seq(self, sentence):
    """
    :param str sentence:
    :rtype: list[int]
    """
    if self.unknown_label is not None:
      seq = [self.vocab.get(k, self.unknown_label_id) for k in sentence]
    else:
      seq = [self.vocab[k] for k in sentence]
    return seq + self.seq_postfix


class Utf8ByteTargets(Vocabulary):
  """
  Uses bytes as target labels from UTF8 encoded text. All bytes (0-255) are allowed.
  Also see :class:`CharacterTargets`.
  """

  def __init__(self, seq_postfix=None):
    """
    :param list[int]|None seq_postfix: labels will be added to the seq in self.get_seq
    """
    super(Utf8ByteTargets, self).__init__(vocab_file=None, seq_postfix=seq_postfix, unknown_label=None)

  def _parse_vocab(self):
    """
    Sets self.vocab, self.labels, self.num_labels.
    """
    self.vocab = {chr(i): i for i in range(256)}
    self.labels = [chr(i) for i in range(256)]
    self.num_labels = 256

  def get_seq(self, sentence):
    """
    :param str sentence:
    :rtype: list[int]
    """
    if sys.version_info[0] >= 3:
      seq = list(sentence.encode("utf8"))
    else:
      seq = list(bytearray(sentence.encode("utf8")))
    return seq + self.seq_postfix


class BlissDataset(CachedDataset2):
  """
  Reads in a Bliss XML corpus (similar to :class:`LmDataset`),
  and provides the features (similar to :class:`TimitDataset`)
  and the orthography as words, subwords or chars (similar to :class:`TranslationDataset`).

  Example:
    ./tools/dump-dataset.py "
      {'class':'BlissDataset',
       'path': '/u/tuske/work/ASR/switchboard/corpus/xml/train.corpus.gz',
       'bpe_file': '/u/zeyer/setups/switchboard/subwords/swb-bpe-codes',
       'vocab_file': '/u/zeyer/setups/switchboard/subwords/swb-vocab'}"
  """

  class SeqInfo:
    """
    Covers all relevant seq info.
    """
    __slots__ = ("idx", "tag", "orth_raw", "orth_seq", "audio_path", "audio_start", "audio_end")

  def __init__(self, path, vocab_file, bpe_file=None,
               num_feature_filters=40, feature_window_len=0.025, feature_step_len=0.010, with_delta=False,
               norm_mean=None, norm_std_dev=None,
               **kwargs):
    """
    :param str path: path to XML. can also be gzipped.
    :param str vocab_file: path to vocabulary file. Python-str which evals to dict[str,int]
    :param str bpe_file: Byte-pair encoding file
    :param int num_feature_filters: e.g. number of MFCCs
    :param bool|int with_delta: whether to add delta features (doubles the features dim). if int, up to this degree
    """
    super(BlissDataset, self).__init__(**kwargs)
    assert norm_mean is None and norm_std_dev is None, "%s, not yet implemented..." % self
    from Util import hms_fraction
    import time
    start_time = time.time()
    self._num_feature_filters = num_feature_filters
    self.num_inputs = num_feature_filters
    self._feature_window_len = feature_window_len
    self._feature_step_len = feature_step_len
    if isinstance(with_delta, bool):
      with_delta = 1 if with_delta else 0
    assert isinstance(with_delta, int)
    self._with_delta = with_delta
    self.num_inputs *= (1 + with_delta)
    self._bpe_file = open(bpe_file, "r")
    self._seqs = []  # type: typing.List[BlissDataset.SeqInfo]
    self._vocab = {}  # type: typing.Dict[str,int]  # set in self._parse_vocab
    self._parse_bliss_xml(filename=path)
    # TODO: loading audio like in TimitDataset, and in parallel
    self._bpe = BytePairEncoding(vocab_file=vocab_file, bpe_file=bpe_file)
    self.labels["classes"] = self._bpe.labels
    self.num_outputs = {'data': (self.num_inputs, 2), "classes": (self._bpe.num_labels, 1)}
    print("%s: Loaded %r, num seqs: %i, elapsed: %s" % (
      self.__class__.__name__, path, len(self._seqs), hms_fraction(time.time() - start_time)), file=log.v3)

  def _parse_bliss_xml(self, filename):
    """
    This takes e.g. around 5 seconds for the Switchboard 300h train corpus.
    Should be as fast as possible to get a list of the segments.
    All further parsing and loading can then be done in parallel and lazily.
    :param str filename:
    :return: nothing, fills self._segments
    """
    # Also see LmDataset._iter_bliss.
    import gzip
    import xml.etree.ElementTree as ElementTree
    corpus_file = open(filename, 'rb')
    if filename.endswith(".gz"):
      corpus_file = gzip.GzipFile(fileobj=corpus_file)
    SeqInfo = self.SeqInfo
    context = iter(ElementTree.iterparse(corpus_file, events=('start', 'end')))
    elem_tree = []
    name_tree = []
    cur_recording = None
    idx = 0
    for event, elem in context:
      if event == "start":
        elem_tree += [elem]
        name_tree += [elem.attrib.get("name", None)]
        if elem.tag == "recording":
          cur_recording = elem.attrib["audio"]
      if event == 'end' and elem.tag == "segment":
        info = SeqInfo()
        info.idx = idx
        info.tag = "/".join(name_tree)
        info.orth_raw = elem.find("orth").text
        info.audio_path = cur_recording
        info.audio_start = float(elem.attrib["start"])
        info.audio_end = float(elem.attrib["end"])
        self._seqs.append(info)
        idx += 1
        if elem_tree:
          elem_tree[0].clear()  # free memory
      if event == "end":
        assert elem_tree[-1] is elem
        elem_tree = elem_tree[:-1]
        name_tree = name_tree[:-1]
    self._num_seqs = len(self._seqs)

  def init_seq_order(self, epoch=None, seq_list=None):
    """
    :param int|None epoch:
    :param list[str] | None seq_list: In case we want to set a predefined order.
    :rtype: bool
    :returns whether the order changed (True is always safe to return)
    """
    super(BlissDataset, self).init_seq_order(epoch=epoch, seq_list=seq_list)
    self._num_seqs = len(self._seqs)
    return True

  def _collect_single_seq(self, seq_idx):
    raise NotImplementedError  # TODO...


class LibriSpeechCorpus(CachedDataset2):
  """
  LibriSpeech. http://www.openslr.org/12/

  "train-*" Seq-length 'data' Stats (default MFCC, every 10ms):
    281241 seqs
    Mean: 1230.94154835176
    Std dev: 383.5126785278322
    Min/max: 84 / 2974
  "train-*" Seq-length 'classes' Stats (BPE with 10k symbols):
    281241 seqs
    Mean: 58.46585312952222
    Std dev: 20.54464373013634
    Min/max: 1 / 161
  "train-*" mean transcription len: 177.009085 (chars), i.e. ~3 chars per BPE label
  """
  def __init__(self, path, prefix, audio,
               orth_post_process=None,
               targets=None, chars=None, bpe=None,
               use_zip=False, use_ogg=False, use_cache_manager=False,
               fixed_random_seed=None, fixed_random_subset=None,
               epoch_wise_filter=None,
               name=None,
               **kwargs):
    """
    :param str path: dir, should contain "train-*/*/*/{*.flac,*.trans.txt}", or "train-*.zip"
    :param str prefix: "train", "dev", "test", "dev-clean", "dev-other", ...
    :param str|list[str]|None orth_post_process: :func:`get_post_processor_function`, applied on orth
    :param str|None targets: "bpe" or "chars" currently, if `None`, then "bpe"
    :param dict[str]|None audio: options for :class:`ExtractAudioFeatures`
    :param dict[str]|None bpe: options for :class:`BytePairEncoding`
    :param dict[str]|None chars: options for :class:`CharacterTargets`
    :param bool use_zip: whether to use the ZIP files instead (better for NFS)
    :param bool use_ogg: add .ogg postfix to all files
    :param bool use_cache_manager: uses :func:`Util.cf`
    :param int|None fixed_random_seed: for the shuffling, e.g. for seq_ordering='random'. otherwise epoch will be used
    :param float|int|None fixed_random_subset:
      Value in [0,1] to specify the fraction, or integer >=1 which specifies number of seqs.
      If given, will use this random subset. This will be applied initially at loading time,
      i.e. not dependent on the epoch. It will use an internally hardcoded fixed random seed, i.e. it's deterministic.
    :param dict|None epoch_wise_filter: see init_seq_order
    """
    if not name:
      name = "prefix:" + prefix
    super(LibriSpeechCorpus, self).__init__(name=name, **kwargs)
    import os
    from glob import glob
    import zipfile
    import Util
    self.path = path
    self.prefix = prefix
    self.use_zip = use_zip
    self.use_ogg = use_ogg
    self._zip_files = None
    if use_zip:
      zip_fn_pattern = "%s/%s*.zip" % (self.path, self.prefix)
      zip_fns = sorted(glob(zip_fn_pattern))
      assert zip_fns, "no files found: %r" % zip_fn_pattern
      if use_cache_manager:
        zip_fns = [Util.cf(fn) for fn in zip_fns]
      self._zip_files = {
        os.path.splitext(os.path.basename(fn))[0]: zipfile.ZipFile(fn)
        for fn in zip_fns}  # e.g. "train-clean-100" -> ZipFile
    assert prefix.split("-")[0] in ["train", "dev", "test"]
    assert os.path.exists(path + "/train-clean-100" + (".zip" if use_zip else ""))
    self.orth_post_process = None
    if orth_post_process:
      from LmDataset import get_post_processor_function
      self.orth_post_process = get_post_processor_function(orth_post_process)
    assert bpe or chars
    if targets == "bpe" or (targets is None and bpe is not None):
      assert bpe is not None and chars is None
      self.bpe = BytePairEncoding(**bpe)
      self.targets = self.bpe
      self.labels = {"classes": self.bpe.labels}
    elif targets == "chars" or (targets is None and chars is not None):
      assert bpe is None and chars is not None
      self.chars = CharacterTargets(**chars)
      self.labels = {"classes": self.chars.labels}
      self.targets = self.chars
    else:
      raise Exception("invalid targets %r. provide bpe or chars" % targets)
    self._fixed_random_seed = fixed_random_seed
    self._audio_random = numpy.random.RandomState(1)
    self.feature_extractor = (
      ExtractAudioFeatures(random_state=self._audio_random, **audio) if audio is not None else None)
    self.num_inputs = self.feature_extractor.get_feature_dimension() if self.feature_extractor else 0
    self.num_outputs = {
      "classes": [self.targets.num_labels, 1], "raw": {"dtype": "string", "shape": ()}}
    if self.feature_extractor:
      self.num_outputs["data"] = [self.num_inputs, 2]
    self.transs = self._collect_trans()
    self._reference_seq_order = sorted(self.transs.keys())
    if fixed_random_subset:
      if 0 < fixed_random_subset < 1:
        fixed_random_subset = int(len(self._reference_seq_order) * fixed_random_subset)
      assert isinstance(fixed_random_subset, int) and fixed_random_subset > 0
      rnd = numpy.random.RandomState(42)
      seqs = self._reference_seq_order
      rnd.shuffle(seqs)
      seqs = seqs[:fixed_random_subset]
      self._reference_seq_order = seqs
      self.transs = {s: self.transs[s] for s in seqs}
    self.epoch_wise_filter = epoch_wise_filter
    self._seq_order = None  # type: typing.Optional[typing.List[int]]
    self.init_seq_order()

  def _collect_trans(self):
    from glob import glob
    import os
    import zipfile
    transs = {}  # type: typing.Dict[typing.Tuple[str,int,int,int],str]  # (subdir, speaker-id, chapter-id, seq-id) -> transcription  # nopep8
    if self.use_zip:
      for name, zip_file in self._zip_files.items():
        assert isinstance(zip_file, zipfile.ZipFile)
        assert zip_file.filelist
        assert zip_file.filelist[0].filename.startswith("LibriSpeech/")
        for info in zip_file.filelist:
          assert isinstance(info, zipfile.ZipInfo)
          path = info.filename.split("/")
          assert path[0] == "LibriSpeech", "does not expect %r (%r)" % (info, info.filename)
          if path[1].startswith(self.prefix):
            subdir = path[1]  # e.g. "train-clean-100"
            assert subdir == name
            if path[-1].endswith(".trans.txt"):
              for l in zip_file.read(info).decode("utf8").splitlines():
                seq_name, txt = l.split(" ", 1)
                speaker_id, chapter_id, seq_id = map(int, seq_name.split("-"))
                if self.orth_post_process:
                  txt = self.orth_post_process(txt)
                transs[(subdir, speaker_id, chapter_id, seq_id)] = txt
    else:  # not zipped, directly read extracted files
      for subdir in glob("%s/%s*" % (self.path, self.prefix)):
        if not os.path.isdir(subdir):
          continue
        subdir = os.path.basename(subdir)  # e.g. "train-clean-100"
        for fn in glob("%s/%s/*/*/*.trans.txt" % (self.path, subdir)):
          for l in open(fn).read().splitlines():
            seq_name, txt = l.split(" ", 1)
            speaker_id, chapter_id, seq_id = map(int, seq_name.split("-"))
            if self.orth_post_process:
              txt = self.orth_post_process(txt)
            transs[(subdir, speaker_id, chapter_id, seq_id)] = txt
      assert transs, "did not found anything %s/%s*" % (self.path, self.prefix)
    assert transs
    return transs

  def init_seq_order(self, epoch=None, seq_list=None):
    """
    If random_shuffle_epoch1, for epoch 1 with "random" ordering, we leave the given order as is.
    Otherwise, this is mostly the default behavior.

    :param int|None epoch:
    :param list[str]|None seq_list: In case we want to set a predefined order.
    :rtype: bool
    :returns whether the order changed (True is always safe to return)
    """
    import Util
    super(LibriSpeechCorpus, self).init_seq_order(epoch=epoch, seq_list=seq_list)
    if not epoch:
      epoch = 1
    self._audio_random.seed(self._fixed_random_seed or self._get_random_seed_for_epoch(epoch=epoch))

    def get_seq_len(i):
      """
      :param int i:
      :rtype: int
      """
      return len(self.transs[self._reference_seq_order[i]])

    if seq_list is not None:
      seqs = [i for i in range(len(self._reference_seq_order)) if self._get_tag(i) in seq_list]
      seqs = {self._get_tag(i): i for i in seqs}
      for seq_tag in seq_list:
        assert seq_tag in seqs, "did not found all requested seqs. we have eg: %s" % (self._get_tag(0),)
      self._seq_order = [seqs[seq_tag] for seq_tag in seq_list]
      self._num_seqs = len(self._seq_order)
    else:
      num_seqs = len(self._reference_seq_order)
      self._seq_order = self.get_seq_order_for_epoch(
        epoch=epoch, num_seqs=num_seqs, get_seq_len=get_seq_len)
      self._num_seqs = len(self._seq_order)
    if self.epoch_wise_filter:
      # Note: A more generic variant of this code is :class:`MetaDataset.EpochWiseFilter`.
      from MetaDataset import EpochWiseFilter
      old_num_seqs = self._num_seqs
      any_filter = False
      for (ep_start, ep_end), value in sorted(self.epoch_wise_filter.items()):
        if ep_start is None:
          ep_start = 1
        if ep_end is None or ep_end == -1:
          ep_end = sys.maxsize
        assert isinstance(ep_start, int) and isinstance(ep_end, int) and 1 <= ep_start <= ep_end
        assert isinstance(value, dict)
        if ep_start <= epoch <= ep_end:
          any_filter = True
          opts = CollectionReadCheckCovered(value.copy())
          if opts.get("subdirs") is not None:
            subdirs = opts.get("subdirs", None)
            assert isinstance(subdirs, list)
            self._seq_order = [idx for idx in self._seq_order if self._reference_seq_order[idx][0] in subdirs]
            assert self._seq_order, "subdir filter %r invalid?" % (subdirs,)
          if opts.get("use_new_filter"):
            if "subdirs" in opts.collection:
              opts.collection.pop("subdirs")
            self._seq_order = EpochWiseFilter.filter_epoch(
              opts=opts, debug_msg_prefix="%s, epoch %i. " % (self, epoch),
              get_seq_len=get_seq_len, seq_order=self._seq_order)
          else:
            if opts.get("max_mean_len"):
              max_mean_len = opts.get("max_mean_len")
              seqs = numpy.array(
                sorted([(len(self.transs[self._reference_seq_order[idx]]), idx) for idx in self._seq_order]))
              # Note: This is somewhat incorrect. But keep the behavior, such that old setups are reproducible.
              # You can use the option `use_new_filter` to get a better behavior.
              num = Util.binary_search_any(
                cmp=lambda num_: numpy.mean(seqs[:num_, 0]) > max_mean_len, low=1, high=len(seqs) + 1)
              assert num is not None
              self._seq_order = list(seqs[:num, 1])
              print(
                ("%s, epoch %i. Old mean seq len (transcription) is %f, new is %f, requested max is %f."
                 " Old num seqs is %i, new num seqs is %i.") %
                (self, epoch, float(numpy.mean(seqs[:, 0])), float(numpy.mean(seqs[:num, 0])), max_mean_len,
                 len(seqs), num),
                file=log.v4)
          opts.assert_all_read()
          self._num_seqs = len(self._seq_order)
      if any_filter:
        print("%s, epoch %i. Old num seqs %i, new num seqs %i." % (
          self, epoch, old_num_seqs, self._num_seqs), file=log.v4)
      else:
        print("%s, epoch %i. No filter for this epoch." % (self, epoch), file=log.v4)
    return True

  def get_current_seq_order(self):
    """
    :rtype: list[int]
    """
    assert self._seq_order is not None
    return self._seq_order

  def _get_ref_seq_idx(self, seq_idx):
    """
    :param int seq_idx:
    :return: idx in self._reference_seq_order
    :rtype: int
    """
    return self._seq_order[seq_idx]

  def have_corpus_seq_idx(self):
    """
    :rtype: bool
    """
    return True

  def get_corpus_seq_idx(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: int
    """
    return self._get_ref_seq_idx(seq_idx)

  def _get_tag(self, ref_seq_idx):
    """
    :param int ref_seq_idx:
    :rtype: str
    """
    subdir, speaker_id, chapter_id, seq_id = self._reference_seq_order[ref_seq_idx]
    return "%(sd)s-%(sp)i-%(ch)i-%(i)04i" % {
      "sd": subdir, "sp": speaker_id, "ch": chapter_id, "i": seq_id}

  def get_tag(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: str
    """
    return self._get_tag(self._get_ref_seq_idx(seq_idx))

  def get_all_tags(self):
    """
    :rtype: list[str]
    """
    return [self._get_tag(i) for i in range(len(self._reference_seq_order))]

  def get_total_num_seqs(self):
    """
    :rtype: int
    """
    return len(self._reference_seq_order)

  def _get_transcription(self, seq_idx):
    """
    :param int seq_idx:
    :return: (bpe, txt)
    :rtype: (list[int], str)
    """
    seq_key = self._reference_seq_order[self._get_ref_seq_idx(seq_idx)]
    targets_txt = self.transs[seq_key]
    return self.targets.get_seq(targets_txt), targets_txt

  def _open_audio_file(self, seq_idx):
    """
    :param int seq_idx:
    :return: io.FileIO
    """
    import io
    import os
    import zipfile
    subdir, speaker_id, chapter_id, seq_id = self._reference_seq_order[self._get_ref_seq_idx(seq_idx)]
    audio_fn = "%(sd)s/%(sp)i/%(ch)i/%(sp)i-%(ch)i-%(i)04i.flac" % {
      "sd": subdir, "sp": speaker_id, "ch": chapter_id, "i": seq_id}
    if self.use_ogg:
      audio_fn += ".ogg"
    if self.use_zip:
      audio_fn = "LibriSpeech/%s" % (audio_fn,)
      zip_file = self._zip_files[subdir]
      assert isinstance(zip_file, zipfile.ZipFile)
      raw_bytes = zip_file.read(audio_fn)
      return io.BytesIO(raw_bytes)
    else:
      audio_fn = "%s/%s" % (self.path, audio_fn)
      assert os.path.exists(audio_fn)
      return open(audio_fn, "rb")

  def _collect_single_seq(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: DatasetSeq
    """
    seq_tag = self.get_tag(seq_idx)
    if self.feature_extractor:
      with self._open_audio_file(seq_idx) as audio_file:
        features = self.feature_extractor.get_audio_features_from_raw_bytes(audio_file, seq_name=seq_tag)
    else:
      features = numpy.zeros(())  # currently the API requires some dummy values...
    bpe, txt = self._get_transcription(seq_idx)
    targets = numpy.array(bpe, dtype="int32")
    raw = numpy.array(txt, dtype="object")
    return DatasetSeq(
      features=features,
      targets={"classes": targets, "raw": raw},
      seq_idx=seq_idx,
      seq_tag=seq_tag)


class OggZipDataset(CachedDataset2):
  """
  Generic dataset which reads a Zip file containing Ogg files for each sequence and a text document.
  The feature extraction settings are determined by the ``audio`` option, which is passed to :class:`ExtractAudioFeatures`.
  Does also support Wav files, and might even support other file formats readable by the 'soundfile'
  library (not tested). By setting ``audio`` or ``targets`` to ``None``, the dataset can be used in
  text only or audio only mode. The content of the zip file is:

    - a .txt file with the same name as the zipfile, containing a python list of dictionaries
    - a subfolder with the same name as the zipfile, containing the audio files

  The dictionaries in the .txt file must have the following structure:

  .. code::

    [{'seq_name': 'arbitrary_sequence_name', 'text': 'some utterance text', 'duration': 2.3, 'file': 'sequence0.wav'}, ...]

  If ``seq_name`` is not included, the seq_tag will be the name of the file. ``duration`` is mandatory, as this information
  is needed for the sequence sorting.

  """

  def __init__(self, path, audio, targets,
               targets_post_process=None,
               use_cache_manager=False, segment_file=None,
               fixed_random_seed=None, fixed_random_subset=None,
               epoch_wise_filter=None,
               **kwargs):
    """
    :param str path: filename to zip
    :param dict[str]|None audio: options for :class:`ExtractAudioFeatures`. use {} for default. None means to disable.
    :param dict[str]|None targets: options for :func:`Vocabulary.create_vocab` (e.g. :class:`BytePairEncoding`)
    :param str|list[str]|((str)->str)|None targets_post_process: :func:`get_post_processor_function`, applied on orth
    :param bool use_cache_manager: uses :func:`Util.cf`
    :param str|None segment_file: specify a .txt or .gz text file containing sequence tags that will be used as whitelist
    :param int|None fixed_random_seed: for the shuffling, e.g. for seq_ordering='random'. otherwise epoch will be used
    :param float|int|None fixed_random_subset:
      Value in [0,1] to specify the fraction, or integer >=1 which specifies number of seqs.
      If given, will use this random subset. This will be applied initially at loading time,
      i.e. not dependent on the epoch. It will use an internally hardcoded fixed random seed, i.e. it's deterministic.
    :param dict|None epoch_wise_filter: see init_seq_order
    """
    import os
    import zipfile
    import Util
    from MetaDataset import EpochWiseFilter
    if not isinstance(path, list) and os.path.splitext(path)[1] != ".zip" and os.path.isdir(path) and os.path.isfile(path + ".txt"):
      # Special case (mostly for debugging) to directly access the filesystem, not via zip-file.
      self.paths = [os.path.dirname(path)]
      self._names = [os.path.basename(path)]
      self._zip_files = None
      assert not use_cache_manager, "cache manager only for zip file"
    else:
      self.paths = path if isinstance(path, list) else [path]
      for path in self.paths:
        assert os.path.splitext(path)[1] == ".zip"
      self._names = [os.path.splitext(os.path.basename(path))[0] for path in self.paths]
      if use_cache_manager:
        self.paths = [Util.cf(path) for path in self.paths]
      self._zip_files = [zipfile.ZipFile(path) for path in self.paths]
    self.segments = None
    if segment_file:
      self._read_segment_list(segment_file)
    kwargs.setdefault("name", self._names[0])
    super(OggZipDataset, self).__init__(**kwargs)
    self.targets = Vocabulary.create_vocab(**targets) if targets is not None else None
    if self.targets:
      self.labels["classes"] = self.targets.labels
    self.targets_post_process = None  # type: typing.Optional[typing.Callable[[str],str]]
    if targets_post_process:
      if callable(targets_post_process):
        self.targets_post_process = targets_post_process
      else:
        from LmDataset import get_post_processor_function
        self.targets_post_process = get_post_processor_function(targets_post_process)
    self._fixed_random_seed = fixed_random_seed
    self._audio_random = numpy.random.RandomState(1)
    self.feature_extractor = (
      ExtractAudioFeatures(random_state=self._audio_random, **audio) if audio is not None else None)
    self.num_inputs = self.feature_extractor.get_feature_dimension() if self.feature_extractor else 0
    self.num_outputs = {"raw": {"dtype": "string", "shape": ()}}
    if self.targets:
      self.num_outputs["classes"] = [self.targets.num_labels, 1]
    if self.feature_extractor:
      self.num_outputs["data"] = [self.num_inputs, 2]
    self._data = self._collect_data()
    if fixed_random_subset:
      self._filter_fixed_random_subset(fixed_random_subset)
    self.epoch_wise_filter = EpochWiseFilter(epoch_wise_filter) if epoch_wise_filter else None
    self._seq_order = None  # type: typing.Optional[typing.List[int]]
    self.init_seq_order()

  def _read(self, filename, zip_index):
    """
    :param str filename: in zip-file
    :param int zip_index: index of the zip file to load, unused when loading without zip
    :rtype: bytes
    """
    if self._zip_files is not None:
      return self._zip_files[zip_index].read(filename)
    return open("%s/%s" % (self.paths[0], filename), "rb").read()

  def _collect_data_part(self, zip_index):
    """
    collect all the entries of a single zip-file or txt file
    :param int zip_index: index of the zip-file in self._zip_files, unused when loading without zip
    :return: data entries
    :rtype: list[dict[str]]
    """
    data = eval(self._read("%s.txt" % self._names[zip_index], zip_index))  # type: typing.List[typing.Dict[str]]
    assert data and isinstance(data, list)
    first_entry = data[0]
    assert isinstance(first_entry, dict)
    assert isinstance(first_entry["text"], str)
    assert isinstance(first_entry["duration"], float)
    # when 'audio' is None and sequence names are given, this dataset can be used in text-only mode
    if "file" in first_entry:
      assert isinstance(first_entry["file"], str)
    else:
      assert self.feature_extractor, "feature extraction is enabled, but no audio files are specified"
      assert isinstance(first_entry["seq_name"], str)
    # add index to data list
    for entry in data:
      entry['_zip_file_index'] = zip_index
    if self.segments:
      data[:] = [entry for entry in data if self._get_tag_from_info_dict(entry) in self.segments]
    return data

  def _collect_data(self):
    """
    :return: entries
    :rtype: list[dict[str]]
    """
    data = []
    if self._zip_files:
      for zip_index in range(len(self._zip_files)):
        zip_data = self._collect_data_part(zip_index)
        data += zip_data
    else:
      # collect data from a txt file
      data = self._collect_data_part(0)
    return data

  def _read_segment_list(self, segment_file):
    """
    read a list of segment names in either plain text or gzip

    :param str segment_file:
    """
    if segment_file.endswith(".gz"):
      import gzip
      segment_file_handle = gzip.open(segment_file)
      self.segments = set([s.decode() for s in segment_file_handle.read().splitlines()])
    else:
      segment_file_handle = open(segment_file)
      self.segments = set(segment_file_handle.read().splitlines())

  def _filter_fixed_random_subset(self, fixed_random_subset):
    """
    :param int fixed_random_subset:
    """
    if 0 < fixed_random_subset < 1:
      fixed_random_subset = int(len(self._data) * fixed_random_subset)
    assert isinstance(fixed_random_subset, int) and fixed_random_subset > 0
    rnd = numpy.random.RandomState(42)
    seqs = self._data
    rnd.shuffle(seqs)
    seqs = seqs[:fixed_random_subset]
    self._data = seqs

  def init_seq_order(self, epoch=None, seq_list=None):
    """
    If random_shuffle_epoch1, for epoch 1 with "random" ordering, we leave the given order as is.
    Otherwise, this is mostly the default behavior.

    :param int|None epoch:
    :param list[str]|None seq_list: In case we want to set a predefined order.
    :rtype: bool
    :returns whether the order changed (True is always safe to return)
    """
    super(OggZipDataset, self).init_seq_order(epoch=epoch, seq_list=seq_list)
    if not epoch:
      epoch = 1
    self._audio_random.seed(self._fixed_random_seed or self._get_random_seed_for_epoch(epoch=epoch))

    def get_seq_len(i):
      """
      Returns the length based on the duration entry of the dataset,
      multiplied by 100 to avoid similar rounded durations.
      It is also used when using the dataset in text-only-mode (`audio` is None).
      :param int i:
      :rtype: int
      """
      return int(self._data[i]["duration"] * 100)

    if seq_list is not None:
      seqs = {seq["file"]: i for i, seq in enumerate(self._data) if seq["file"] in seq_list}
      for seq_tag in seq_list:
        assert seq_tag in seqs, "did not found all requested seqs. we have eg: %s" % (self._data[0]["file"],)
      self._seq_order = [seqs[seq_tag] for seq_tag in seq_list]
      self._num_seqs = len(self._seq_order)
    else:
      num_seqs = len(self._data)
      self._seq_order = self.get_seq_order_for_epoch(
        epoch=epoch, num_seqs=num_seqs, get_seq_len=get_seq_len)
      if self.epoch_wise_filter:
        self.epoch_wise_filter.debug_msg_prefix = str(self)
        self._seq_order = self.epoch_wise_filter.filter(epoch=epoch, seq_order=self._seq_order, get_seq_len=get_seq_len)
      self._num_seqs = len(self._seq_order)

    return True

  def get_current_seq_order(self):
    """
    :rtype: list[int]
    """
    assert self._seq_order is not None
    return self._seq_order

  def _get_ref_seq_idx(self, seq_idx):
    """
    :param int seq_idx:
    :return: idx in self._reference_seq_order
    :rtype: int
    """
    return self._seq_order[seq_idx]

  def have_corpus_seq_idx(self):
    """
    :rtype: bool
    """
    return True

  def get_corpus_seq_idx(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: int
    """
    return self._get_ref_seq_idx(seq_idx)

  @staticmethod
  def _get_tag_from_info_dict(info):
    """
    :param dict[str] info:
    :rtype: str
    """
    return info.get("seq_name", info["file"])

  def get_tag(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: str
    """
    return self._get_tag_from_info_dict(self._data[self._get_ref_seq_idx(seq_idx)])

  def get_all_tags(self):
    """
    :rtype: list[str]
    """
    return [self._get_tag_from_info_dict(seq) for seq in self._data]

  def get_total_num_seqs(self):
    """
    :rtype: int
    """
    return len(self._data)

  def _get_transcription(self, seq_idx):
    """
    :param int seq_idx:
    :return: (targets (e.g. bpe), txt)
    :rtype: (list[int], str)
    """
    seq = self._data[self._get_ref_seq_idx(seq_idx)]
    raw_targets_txt = seq["text"]
    targets_txt = raw_targets_txt
    if self.targets:
      if self.targets_post_process:
        targets_txt = self.targets_post_process(targets_txt)
      targets_seq = self.targets.get_seq(targets_txt)
    else:
      targets_seq = []
    return targets_seq, raw_targets_txt

  def _open_audio_file(self, seq_idx):
    """
    :param int seq_idx:
    :return: io.FileIO
    """
    import io
    seq = self._data[self._get_ref_seq_idx(seq_idx)]
    audio_fn = "%s/%s" % (self._names[seq['_zip_file_index']], seq["file"])
    raw_bytes = self._read(audio_fn, seq['_zip_file_index'])
    return io.BytesIO(raw_bytes)

  def _collect_single_seq(self, seq_idx):
    """
    :param int seq_idx:
    :rtype: DatasetSeq
    """
    seq_tag = self.get_tag(seq_idx)
    if self.feature_extractor:
      with self._open_audio_file(seq_idx) as audio_file:
        features = self.feature_extractor.get_audio_features_from_raw_bytes(audio_file, seq_name=seq_tag)
    else:
      features = numpy.zeros(())  # currently the API requires some dummy values...
    targets, txt = self._get_transcription(seq_idx)
    targets = numpy.array(targets, dtype="int32")
    txt = numpy.array(txt, dtype="object")
    return DatasetSeq(
      features=features,
      targets={"classes": targets, "raw": txt},
      seq_idx=seq_idx,
      seq_tag=seq_tag)


class Enwik8Corpus(CachedDataset2):
  """
  enwik8
  """
  # Use a single HDF file, and cache it across all instances.
  _hdf_file = None

  def __init__(self, path, subset, seq_len, fixed_random_seed=None, batch_num_seqs=None, subsubset=None,
               **kwargs):
    """
    :param str path:
    :param str subset: "training", "validation", "test"
    :param int seq_len:
    :param int|None fixed_random_seed:
    :param int|None batch_num_seqs: if given, will not shuffle the data but have it in such order,
      that with a given batch num_seqs setting, you could reuse the hidden state in an RNN
    :param int|(int,int)|None subsubset: end, (start,end), or full
    """
    assert subset in ["training", "validation", "test"]
    import os
    super(Enwik8Corpus, self).__init__(**kwargs)
    self.path = path
    assert os.path.isdir(path)
    self._prepare()
    self._unique = self._hdf_file.attrs['unique']  # array label-idx -> byte idx (uint8, 0-255)
    labels = [bytes([b]) for b in self._unique]
    self.labels = {"data": labels, "classes": labels}
    self.num_inputs = len(labels)
    self.num_outputs = {"data": [self.num_inputs, 1], "classes": [self.num_inputs, 1]}
    self._data = self._hdf_file["split/%s/default" % subset]  # raw data, uint8 array
    if subsubset:
      if isinstance(subsubset, int):
        self._data = self._data[:subsubset]
      else:
        self._data = self._data[subsubset[0]:subsubset[1]]
    assert len(self._data) > 1
    self._seq_len = seq_len
    self._fixed_random_seed = fixed_random_seed
    self._batch_num_seqs = batch_num_seqs
    self._random = numpy.random.RandomState(1)  # seed will be set in init_seq_order
    self._seq_starts = numpy.arange(0, len(self._data) - 1, seq_len)
    self._seq_order = None  # type: typing.Optional[typing.List[int]]

  def get_data_dtype(self, key):
    """
    :param str key:
    :rtype: str
    """
    return "uint8"

  def init_seq_order(self, epoch=None, seq_list=None):
    """
    :param int epoch:
    :param list[str]|None seq_list:
    :rtype: bool
    """
    super(Enwik8Corpus, self).init_seq_order(epoch=epoch, seq_list=seq_list)
    if not epoch:
      epoch = 1
    epoch_part = None
    if self.partition_epoch:
      epoch_part = (epoch - 1) % self.partition_epoch
      epoch = ((epoch - 1) // self.partition_epoch) + 1
    self._random.seed(self._fixed_random_seed or self._get_random_seed_for_epoch(epoch=epoch))
    self._num_seqs = len(self._seq_starts)
    self._num_timesteps = len(self._data) - 1
    if self._batch_num_seqs is None:
      self._seq_order = self.get_seq_order_for_epoch(
        epoch=epoch or 1, num_seqs=self._num_seqs, get_seq_len=lambda _: self._seq_len)
    else:
      if self._num_seqs % self._batch_num_seqs > 0:
        self._num_seqs -= self._num_seqs % self._batch_num_seqs
        self._num_timesteps = None
        assert self._num_seqs > 0
      assert self._num_seqs % self._batch_num_seqs == 0
      seq_index = numpy.array(list(range(self._num_seqs)))
      seq_index = seq_index.reshape((self._batch_num_seqs, self._num_seqs // self._batch_num_seqs))
      seq_index = seq_index.transpose()
      seq_index = seq_index.flatten()
      self._seq_order = seq_index
      if self.partition_epoch:
        assert self._num_seqs >= self.partition_epoch
        partition_epoch_num_seqs = [self._num_seqs // self.partition_epoch] * self.partition_epoch
        i = 0
        while sum(partition_epoch_num_seqs) < self._num_seqs:
          partition_epoch_num_seqs[i] += 1
          i += 1
          assert i < self.partition_epoch
        assert sum(partition_epoch_num_seqs) == self._num_seqs
        self._num_seqs = partition_epoch_num_seqs[epoch_part]
        i = 0
        for n in partition_epoch_num_seqs[:epoch_part]:
          i += n
        self._seq_order = seq_index[i:i + self._num_seqs]
    self._num_seqs = len(self._seq_order)
    return True

  def _collect_single_seq(self, seq_idx):
    idx = self._seq_order[seq_idx]
    src_seq_start = self._seq_starts[idx]
    tgt_seq_start = src_seq_start + 1
    tgt_seq_end = min(tgt_seq_start + self._seq_len, len(self._data))
    src_seq_end = tgt_seq_end - 1
    assert tgt_seq_end - tgt_seq_start == src_seq_end - src_seq_start > 0
    data = numpy.array(self._data[src_seq_start:tgt_seq_end], dtype="uint8")
    return DatasetSeq(
      seq_idx=seq_idx,
      features=data[:-1],
      targets=data[1:],
      seq_tag="offset_%i_%i" % (src_seq_start, src_seq_end - src_seq_start))

  @property
  def _hdf_filename(self):
    return self.path + "/enwik8.hdf5"

  @property
  def _zip_filename(self):
    return self.path + "/enwik8.zip"

  def _prepare(self):
    """
    Reference:
    https://github.com/julian121266/RecurrentHighwayNetworks/blob/master/data/create_enwik8.py
    """
    if self._hdf_file:
      return
    import os
    import h5py
    if not os.path.exists(self._hdf_filename):
      self._create_hdf()
    Enwik8Corpus._hdf_file = h5py.File(self._hdf_filename, "r")

  def _create_hdf(self):
    import os
    import h5py
    import zipfile

    if not os.path.exists(self._zip_filename):
      self._download_zip()

    print("%s: create %s" % (self, self._hdf_filename), file=log.v2)
    num_test_chars = 5000000

    raw_data = zipfile.ZipFile(self._zip_filename).read('enwik8')
    raw_data = numpy.fromstring(raw_data, dtype=numpy.uint8)
    unique, data = numpy.unique(raw_data, return_inverse=True)

    train_data = data[: -2 * num_test_chars]
    valid_data = data[-2 * num_test_chars: -num_test_chars]
    test_data = data[-num_test_chars:]

    f = h5py.File(self._hdf_filename, "w")
    f.attrs['unique'] = unique

    variant = f.create_group('split')
    group = variant.create_group('training')
    group.create_dataset(name='default', data=train_data, compression='gzip')

    group = variant.create_group('validation')
    group.create_dataset(name='default', data=valid_data, compression='gzip')

    group = variant.create_group('test')
    group.create_dataset(name='default', data=test_data, compression='gzip')

    f.close()

  def _download_zip(self):
    url = 'http://mattmahoney.net/dc/enwik8.zip'
    print("%s: download %s" % (self, url), file=log.v2)
    # noinspection PyPackageRequirements
    from six.moves.urllib.request import urlretrieve
    urlretrieve(url, self._zip_filename)


def demo():
  """
  Some demo for some of the :class:`GeneratingDataset`.
  """
  import better_exchook
  better_exchook.install()
  log.initialize(verbosity=[5])
  import sys
  dsclazzeval = sys.argv[1]
  dataset = eval(dsclazzeval)
  assert isinstance(dataset, Dataset)
  assert isinstance(dataset, GeneratingDataset), "use tools/dump-dataset.py for a generic demo instead"
  # noinspection PyProtectedMember
  assert dataset._input_classes and dataset._output_classes
  assert dataset.num_outputs["data"][1] == 2  # expect 1-hot
  assert dataset.num_outputs["classes"][1] == 1  # expect sparse
  for i in range(10):
    print("Seq idx %i:" % i)
    s = dataset.generate_seq(i)
    assert isinstance(s, DatasetSeq)
    features = s.features["data"]
    output_seq = s.features["classes"]
    assert features.ndim == 2
    assert output_seq.ndim == 1
    input_seq = numpy.argmax(features, axis=1)
    # noinspection PyProtectedMember
    input_seq_str = "".join([dataset._input_classes[i] for i in input_seq])
    # noinspection PyProtectedMember
    output_seq_str = "".join([dataset._output_classes[i] for i in output_seq])
    print(" %r" % input_seq_str)
    print(" %r" % output_seq_str)
    assert features.shape[1] == dataset.num_outputs["data"][0]
    assert features.shape[0] == output_seq.shape[0]


if __name__ == "__main__":
  demo()
