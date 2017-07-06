
from __future__ import print_function

import tensorflow as tf
from tensorflow.python.client import device_lib
import contextlib
import os
import sys
from Util import NotSpecified


def tf_version_tuple():
  """
  :return: version tuple, e.g. (1, 1, 0), parsed from tf.__version__
  :rtype: tuple[int]
  """
  return tuple([int(i) for i in tf.__version__.split(".")])


def assert_min_tf_version(version, reason):
  """
  :param tuple[int] version: e.g. (1,2,0) or (1,2)
  :param str reason:
  """
  tf_version = tf_version_tuple()
  assert len(version) <= len(tf_version)
  assert tf_version >= version, "Your TF version %r is too old (older than %r). %s" % (tf_version, version, reason)


class Data(object):
  """
  This class is to describe a tensor,
  i.e. it's shape and properties like
  whether we should consider it as sparse data (i.e. it represents indices).
  This is used in TFNetwork to describe the dataset external data
  as well as for every layer output.
  """

  size_dtype = "int32"

  def __init__(self, name,
               shape=None, dtype=None,
               placeholder=None,
               sparse=None,
               dim=None,
               size_placeholder=None,
               batch_dim_axis=0,
               time_dim_axis=NotSpecified,
               available_for_inference=True,
               auto_create_placeholders=False,
               beam_size=None):
    """
    :param str name:
    :param tuple[int|None]|list[int|None] shape: including time-dim (can be None). excluding batch-dim.
      e.g. (time,feat)=(None,128)
    :param str dtype: e.g. "float32" or "int64"
    :param tf.Tensor|None placeholder: with added batch-dim
    :param bool sparse: whether to treat the value as an index. do not confuse with tf.SparseTensor
    :param None|int dim: feature dimension, shape[-1] if not sparse, otherwise like num_classes
    :param int batch_dim_axis: where we add the batch-dim.
      e.g. shape=(time,...), 0 -> (batch,time,...), 1 -> (time,batch,...)
    :param int|None time_dim_axis: where we have the time dim axis, after we added the batch-dim.
      this is often 1. however, can be None if there is no time-dim.
    :param dict[int,tf.Tensor] tf.Tensor size_placeholder: for every None in shape, this will describe the size.
      The size is always a tensor of shape (batch,), i.e. the size can be different for each sequence in a batch.
    :param bool available_for_inference: e.g. the extern data "classes" is usually not available for inference
    :param int|None beam_size: the batch-dim could be extended by a beam-size,
      such that it represents the merged dims [batch, beam_size].
    """
    self.name = name
    if sparse is None:
      if dtype and (dtype.startswith("int") or dtype.startswith("uint")):
        sparse = True
      else:
        sparse = False
    self.sparse = sparse
    if shape is None:
      assert dim, "no shape specified, need dim"
      if sparse:
        shape = (None,)  # assume common (time,)
      else:
        shape = (None, dim)  # assume common (time,feat)
    self.shape = tuple(shape)  # type: tuple[int|None]  # excluding batch-dim. see self.batch_shape
    if dtype is None:
      if sparse:
        dtype = "int32"
      else:
        dtype = "float32"
    if dim is None and len(shape):
      assert not sparse, "need dim"
      dim = shape[-1]
    self.dim = dim  # type: int
    self.batch_dim_axis = batch_dim_axis  # type: int
    if time_dim_axis is NotSpecified:
      if (sparse and len(shape) >= 1) or ((not sparse) and len(shape) >= 2):
        if batch_dim_axis >= 1:
          time_dim_axis = 0
        else:
          time_dim_axis = 1
      else:
        time_dim_axis = None
    self.time_dim_axis = time_dim_axis  # type: int|None  # counted with batch-dim
    self.dtype = dtype  # type: str
    if placeholder is None and auto_create_placeholders:
      with tf.name_scope("extern_data/placeholders/%s/" % name):
        placeholder = tf.placeholder(**self.get_placeholder_kwargs(with_batch=True))
    self.placeholder = placeholder  # type: tf.Tensor  # this will hold the data value itself
    # The size_placeholder is for each variable length dimension in shape, i.e. excluding the batch-dim.
    if size_placeholder is None and auto_create_placeholders:
      size_placeholder = {}  # type: dict[int,tf.Tensor]
      with tf.name_scope("extern_data/placeholders/%s/" % name):
        for axis in self.get_axes_with_size():
          size_placeholder[axis] = tf.placeholder(**self.get_size_placeholder_kwargs(axis))
    if not size_placeholder and self.ndim_dense <= 1:
      size_placeholder = {}
    self.size_placeholder = size_placeholder  # type: dict[int,tf.Tensor]  # axis w.o. batch -> size of shape (batch,)
    self.available_for_inference = available_for_inference
    self.beam_size = beam_size

  def get_placeholder_kwargs(self, with_batch=True):
    return dict(name=self.name, dtype=self.dtype, shape=self.batch_shape if with_batch else self.shape)

  def get_axes_with_size(self):
    """
    :return: list of axes which can vary in size for each entry of the batch-dim, e.g. the time-dim-axis.
      The axis index is counted without the batch-dim.
    :rtype: list[int]
    """
    return [i for (i, dim) in enumerate(self.shape) if dim is None]

  def get_size_placeholder_kwargs(self, axis, with_batch=True):
    # For each batch a separate size.
    return dict(name="%s_dim%i_size" % (self.name, axis), dtype=self.size_dtype,
                shape=(None,) if with_batch else ())

  def get_kwargs(self):
    keys = ["name", "shape", "dtype", "sparse", "dim", "batch_dim_axis", "time_dim_axis"]
    if not self.available_for_inference:
      keys += ["available_for_inference"]
    if self.beam_size is not None:
      keys += ["beam_size"]
    return {key: getattr(self, key) for key in keys}

  def get_description(self, with_name=True, with_placeholder=False):
    keys = ["shape", "dtype"]
    if self.sparse:
      keys.append("sparse")
      keys.append("dim")
    if self.batch_dim_axis != 0:
      keys.append("batch_dim_axis")
    if self.time_dim_axis is None or self.time_dim_axis >= 2:
      keys.append("time_dim_axis")
    if with_name:
      keys.insert(0, "name")
    if with_placeholder:
      keys.append("placeholder")
    if not self.available_for_inference:
      keys.append("available_for_inference")
    if self.beam_size is not None:
      keys.append("beam_size")
    return "Data(%s)" % ", ".join(["%s=%r" % (key, getattr(self, key)) for key in keys])

  def __repr__(self):
    return self.get_description()

  def copy(self):
    """
    :return: copy of myself, using self.get_kwargs(), and with placeholder and size_placeholder
    :rtype: Data
    """
    data = Data(**self.get_kwargs())
    data.placeholder = self.placeholder
    if self.size_placeholder is not None:
      data.size_placeholder = self.size_placeholder.copy()
    return data

  def copy_as_batch_major(self):
    """
    :return: copy of myself with batch_dim_axis == 0
    :rtype: Data
    """
    data = self.copy()
    if data.batch_dim_axis != 0:
      if data.placeholder is not None:
        data.placeholder = swapaxes(data.placeholder, 0, data.batch_dim_axis)
      if data.time_dim_axis is not None and data.time_dim_axis <= data.batch_dim_axis:
        data.time_dim_axis += 1
      data.batch_dim_axis = 0
    return data

  def copy_as_time_major(self):
    """
    :return: copy of myself with time_dim_axis == 0
    :rtype: Data
    """
    assert self.time_dim_axis is not None
    data = self.copy()
    if data.time_dim_axis != 0:
      if data.placeholder is not None:
        data.placeholder = swapaxes(data.placeholder, 0, data.time_dim_axis)
      if data.batch_dim_axis <= data.time_dim_axis:
        data.batch_dim_axis += 1
      data.time_dim_axis = 0
    return data

  def copy_extend_with_beam(self, beam_size):
    """
    :param int beam_size:
    :return: copy of myself where the batch-dim is extended/multiplied by beam_size, using tile_transposed
    :rtype: Data
    """
    with tf.name_scope("data_extend_with_beam"):
      data = self.copy()
      if data.beam_size and data.beam_size == beam_size:
        return data
      assert data.beam_size is None, "incompatible beam sizes (%r vs %r)" % (data.beam_size, beam_size)
      data.placeholder = tile_transposed(data.placeholder, axis=data.batch_dim_axis, multiples=beam_size)
      data.size_placeholder = {
        i: tile_transposed(v, axis=0, multiples=beam_size) for (i, v) in data.size_placeholder.items()}
      data.beam_size = beam_size * (data.beam_size or 1)
      return data

  def copy_template(self, name=None):
    """
    :return: copy of myself, using self.get_kwargs(), without placeholder
    :rtype: Data
    """
    kwargs = self.get_kwargs()
    if name:
      kwargs["name"] = name
    return Data(**kwargs)

  def copy_template_excluding_time_dim(self, name=None):
    """
    :param str|None name: if set, this will be the new name
    :return: copy of myself excluding the time-dimension without placeholder
    :rtype: Data
    """
    assert self.time_dim_axis is not None
    new_shape = list(self.shape)
    del new_shape[self.time_dim_axis_excluding_batch]
    kwargs = self.get_kwargs()
    kwargs["batch_dim_axis"] = (
      self.batch_dim_axis
      if (self.batch_dim_axis < self.time_dim_axis)
      else (self.batch_dim_axis - 1))
    kwargs["time_dim_axis"] = None
    kwargs["shape"] = new_shape
    if name:
      kwargs["name"] = name
    return Data(**kwargs)

  def copy_template_adding_time_dim(self, name=None, time_dim_axis=0):
    """
    :param str|None name: if set, this will be the new name
    :param int time_dim_axis: the new time-dim-axis index
    :return: copy of myself adding the time-dimension without placeholder
    :rtype: Data
    """
    assert self.time_dim_axis is None
    new_shape = list(self.shape)
    new_shape.insert(time_dim_axis, None)
    kwargs = self.get_kwargs()
    kwargs["batch_dim_axis"] = (
      self.batch_dim_axis
      if (self.batch_dim_axis < time_dim_axis)
      else (self.batch_dim_axis + 1))
    kwargs["time_dim_axis"] = time_dim_axis
    kwargs["shape"] = new_shape
    if name:
      kwargs["name"] = name
    return Data(**kwargs)

  def _get_variable_dim_pattern(self):
    """
    :return: tuple with bools specifying which dims of the shape (excluding batch-dim) are of variable length.
     e.g. (time,feature), shape=(None,128), this returns (True, False)
    :rtype: tuple[bool]
    """
    return tuple([dim is None for dim in self.shape])

  def _get_var_len_axes(self):
    return sorted([i for (i, d) in enumerate(self._get_variable_dim_pattern()) if d])

  def matches_var_dim_pattern(self, other):
    """
    :param Data other:
    :return: whether the variable-dims pattern matches,
      i.e. same variable dims (get_variable_dim_pattern), same time dim, excluding batch-dim.
      i.e. the size_placeholder should be compatible.
    :rtype: bool
    """
    if self.time_dim_axis_excluding_batch != other.time_dim_axis_excluding_batch:
      return False
    return self._get_var_len_axes() == other._get_var_len_axes()

  @property
  def batch_shape(self):
    """
    :return: shape with added batch-dim. e.g. (batch,time,feat) = (None,None,128)
    :rtype: tuple[int|None]
    """
    return self.shape[:self.batch_dim_axis] + (None,) + self.shape[self.batch_dim_axis:]

  @property
  def shape_dense(self):
    if self.sparse:
      return self.shape + (self.dim,)
    return self.shape

  @property
  def ndim(self):
    """
    :rtype: int
    :return: ndim counted without batch-dim
    """
    return len(self.shape)

  @property
  def ndim_dense(self):
    """
    :rtype: int
    :return: ndim counted without batch-dim, added by 1 if we are sparse
    """
    if self.sparse:
      return self.ndim + 1
    return self.ndim

  @property
  def batch_ndim(self):
    """
    :rtype: int
    :return: ndim counted with batch-dim
    """
    return self.ndim + 1

  @property
  def is_time_major(self):
    """
    :return: whether this is in time-major format, i.e. (time,batch,...)
    :rtype: bool
    """
    return self.time_dim_axis == 0

  @property
  def is_batch_major(self):
    """
    :return: whether this is in batch-major format, i.e. (batch,...)
    :rtype: bool
    """
    return self.batch_dim_axis == 0

  @property
  def time_dim_axis_excluding_batch(self):
    if self.time_dim_axis is None:
      return None
    return self.get_batch_axis_excluding_batch(self.time_dim_axis)

  def get_placeholder_as_time_major(self):
    if self.is_time_major:
      assert self.batch_dim_axis == 1
      return self.placeholder
    assert self.batch_dim_axis == 0
    assert self.time_dim_axis == 1
    return swapaxes(self.placeholder, 0, 1)  # (time,batch,dim)

  def get_placeholder_as_batch_major(self):
    if self.batch_dim_axis == 0:
      return self.placeholder
    return swapaxes(self.placeholder, 0, self.batch_dim_axis)  # (time,batch,dim)

  def get_placeholder_with_specific_batch_dim_axis(self, batch_dim_axis):
    if self.batch_dim_axis == batch_dim_axis:
      return self.placeholder
    return swapaxes(self.placeholder, batch_dim_axis, self.batch_dim_axis)

  def get_placeholder_time_flattened(self):
    assert self.have_time_axis()
    # flatten_with_seq_len_mask only works for these two cases at the moment:
    assert (self.time_dim_axis, self.batch_dim_axis) == (0, 1) or (self.time_dim_axis, self.batch_dim_axis) == (1, 0)
    seq_lens = self.size_placeholder[self.time_dim_axis_excluding_batch]
    return flatten_with_seq_len_mask(self.placeholder, seq_lens, time_major=self.is_time_major)

  def get_placeholder_flattened(self, keep_dims=False):
    """
    :param bool keep_dims: if set, it will add broadcast dimensions after the flattening behind the first axis
    :rtype: tf.Tensor
    :return: placeholder where all dynamic axes are flattened into a single axis.
      e.g. for the usual case (batch, time, dim), it becomes (batch'|time', dim),
      or (batch, time, height, dim) will also become (batch'|time', dim).
      with keep_dims, (batch, time, height, dim) will become (batch'|time', 1, 1, dim).
    """
    x = self.placeholder
    dyn_axes = self.get_spatial_batch_axes() + [self.batch_dim_axis]
    if dyn_axes == [self.batch_dim_axis]:
      return x
    assert 0 in dyn_axes, "would need some transpose, not supported at the moment"
    assert len(dyn_axes) > 1
    orig_num_dyn_axes = len(dyn_axes)
    ndim = len(self.batch_shape)
    if self.have_time_axis():
      x = self.get_placeholder_time_flattened()
      removed_axis = max(self.time_dim_axis, self.batch_dim_axis)
      dyn_axes.remove(removed_axis)
      dyn_axes = [(i if (i < removed_axis) else (i - 1))
                  for i in dyn_axes]
      ndim -= 1
    if len(dyn_axes) > 1:
      assert 0 in dyn_axes, "would need some transpose, not supported at the moment"
      for i in dyn_axes:
        if i > 0:
          assert i - 1 in dyn_axes, "would need some transpose, not supported at the moment"
      shape = tf.shape(x)
      x = tf.reshape(
        x,
        [tf.reduce_prod([shape[i] for i in dyn_axes])] +
        [shape[i] for i in range(ndim) if i not in dyn_axes])
      dyn_axes = [0]
    assert dyn_axes == [0]
    if keep_dims and orig_num_dyn_axes >= 2:
      for i in range(orig_num_dyn_axes - 1):
        x = tf.expand_dims(x, axis=1)
    return x

  @property
  def feature_dim_axis(self):
    if self.sparse:
      return None
    return self.batch_ndim - 1

  def get_axes(self, exclude_time=False, exclude_batch=False):
    """
    :param bool exclude_time: will filter out the time-axis
    :param bool exclude_batch: will filter out the batch-axis
    :return: list of axes, like `range(len(self.shape))`, calculated with batch dim.
    :rtype: list[int]
    """
    axes = list(range(len(self.batch_shape)))
    if exclude_time and self.time_dim_axis is not None:
      axes.pop(axes.index(self.time_dim_axis))
    if exclude_batch and self.batch_dim_axis is not None:
      axes.pop(axes.index(self.batch_dim_axis))
    return axes

  def get_axes_from_description(self, axes):
    """
    :param int|list[int]|str|list[str] axes: one axis or multiple axis.
      This is counted with batch-dim, which by default is axis 0 (see enforce_batch_dim_axis).
      It also accepts the special tokens "B"|"batch", "spatial", "spatial_except_time", or "F"|"feature",
      and more (see the code).
    :return: list of axes, counted with batch-dim
    :rtype: list[int]
    """
    if isinstance(axes, str):
      import re
      axes = axes.lower()
      if axes in ["b", "batch"]:
        axes = self.batch_dim_axis
      elif axes == "spatial":
        axes = self.get_spatial_batch_axes()
      elif re.match("(s|spatial):-?\\d+$", axes):
        s = int(axes.split(":")[1])
        axes = self.get_spatial_batch_axes()
        assert s < len(axes)
        axes = axes[s]
      elif axes == "spatial_except_time":
        axes = self.get_spatial_batch_axes()
        assert self.time_dim_axis is not None
        axes.remove(self.time_dim_axis)
      elif axes in ["t", "time"]:
        assert self.time_dim_axis is not None
        axes = self.time_dim_axis
      elif axes == "except_time":
        axes = list(range(self.batch_ndim))
        axes.remove(self.batch_dim_axis)
        assert self.time_dim_axis is not None
        axes.remove(self.time_dim_axis)
      elif axes in ["f", "feature", "non_spatial"]:
        axes = self.get_feature_batch_axes()
      elif all([a in "btf" for a in axes]):
        return self.get_axes_from_description(list(axes))
      else:
        raise Exception("invalid axis mode %r" % axes)
    if isinstance(axes, int):
      axes = [axes]
    assert isinstance(axes, (tuple, list)), "invalid axis %r" % axes
    flat_axes = []
    for i in axes:
      if isinstance(i, int):
        flat_axes += [i]
      else:
        assert isinstance(i, (str, tuple, list))
        flat_axes += self.get_axes_from_description(i)
    flat_axes = [i % self.batch_ndim for i in flat_axes]
    res = []
    for i in flat_axes:
      if i not in res:
        res.append(i)
    return res

  def get_axis_from_description(self, axis):
    """
    :param int|str axis:
    :return: axis, counted with batch-dim
    :rtype: int
    """
    axes = self.get_axes_from_description(axis)
    assert len(axes) == 1, "%r is not a unique axis but %r" % (axis, axes)
    return axes[0]

  def get_batch_axis_excluding_batch(self, axis):
    if axis == self.batch_dim_axis:
      return None
    if axis < self.batch_dim_axis:
      return axis
    return axis - 1

  def get_batch_axis(self, axis):
    if axis >= self.batch_dim_axis:
      return axis + 1
    return axis

  def have_time_axis(self):
    return self.time_dim_axis is not None

  def get_sequence_lengths(self):
    """
    :return: seq lens tensor of shape (batch,) of dtype int32
    :rtype: tf.Tensor
    """
    assert self.time_dim_axis is not None
    return self.size_placeholder[self.time_dim_axis_excluding_batch]

  def get_spatial_batch_axes(self):
    """
    :rtype: list[int]
    :return: list of axes which are not feature and batch axes, counted with batch-dim.
    """
    return [axis
            for axis in range(self.batch_ndim)
            if (axis not in [self.batch_dim_axis, self.feature_dim_axis])]

  def get_spatial_axes(self):
    """
    :rtype: list[int]
    :return: list of axes which are not feature and batch axes, counted without batch-dim.
    """
    return [self.get_batch_axis_excluding_batch(axis) for axis in self.get_spatial_batch_axes()]

  def get_feature_batch_axes(self):
    """
    :rtype: list[int]
    :return: list of axes which are feature axes, counted with batch-dim. currently there is only one or zero such axis.
    """
    if self.feature_dim_axis is not None:
      return [self.feature_dim_axis]
    return []

  def get_feature_axes(self):
    """
    :rtype: list[int]
    :return: list of axes which are feature axes, counted without batch-dim.
    """
    return [self.get_batch_axis_excluding_batch(axis) for axis in self.get_feature_batch_axes()]

  def get_bc_spatial_batch_shape(self):
    """
    :return: shape which will broadcast along all spatial dimensions and time/batch dim
    :rtype: tuple[int]
    """
    dyn_axes = self.get_spatial_batch_axes() + [self.batch_dim_axis]
    return [1 if (axis in dyn_axes) else dim
            for axis, dim in enumerate(self.batch_shape)]


class OutputWithActivation(object):
  def __init__(self, x, act_func=None):
    """
    :param tf.Tensor x:
    :param None|(tf.Tensor)->tf.Tensor act_func:
    """
    self.x = x
    self.act_func = act_func
    if act_func:
      with tf.name_scope("activation"):
        self.y = act_func(x)
    else:
      self.y = x

  def is_softmax_act_func(self):
    return self.act_func is tf.nn.softmax

  def get_logits(self):
    """
    :rtype: tf.Tensor
    :return: logits. logits are (not necessarily normalized) log probabilities, i.e. the input of softmax.
    This call assumes that self.y is in probability space.
    """
    if self.is_softmax_act_func():
      return self.x
    return tf.log(self.y)


def variable_summaries(var, name):
  """Attach a lot of summaries to a Tensor (for TensorBoard visualization)."""
  with tf.name_scope('summaries_%s' % name):
    mean = tf.reduce_mean(var)
    tf.summary.scalar('%s_mean' % name, mean)
    with tf.name_scope('stddev'):
      stddev = tf.sqrt(tf.reduce_mean(tf.square(var - mean)))
    tf.summary.scalar('%s_stddev' % name, stddev)
    tf.summary.scalar('%s_max' % name, tf.reduce_max(var))
    tf.summary.scalar('%s_min' % name, tf.reduce_min(var))
    tf.summary.histogram('%s_histogram' % name, var)


def get_current_var_scope_name():
  """
  :return: current absolute variable scope name, via tf.variable_scope
  :rtype: str
  """
  v = tf.get_variable_scope()
  return v.name


def get_current_name_scope():
  """
  :return: current absolute name scope, via tf.name_scope
  :rtype: str

  http://stackoverflow.com/questions/40907769/how-to-get-current-tensorflow-name-scope

  Note that this is a private member and might break at some point.
  Note also that this does not need to be the same as get_current_var_scope_name().
  """
  return tf.get_default_graph()._name_stack or ""


@contextlib.contextmanager
def reuse_name_scope(name, absolute=None):
  """
  :param str|tf.VariableScope name: relative name scope (absolute if absolute=True or if tf.VariableScope)
  :param bool absolute: if True it will be absolute

  We try to both set the variable scope and the name scope.
  """
  if isinstance(name, tf.VariableScope):
    name = name.name
    if absolute is not None:
      assert absolute is True
    absolute = True
  assert isinstance(name, str)
  assert name
  if not absolute:
    # First figure out the absolute name scope which we want to reuse/set.
    # The current name scope is more reliable because tf.variable_scope
    # will always also set the name scope.
    current_name_scope = get_current_name_scope()
    if current_name_scope:
      name = current_name_scope + "/" + name
  else:
    current_name_scope = None  # not needed
  assert name[-1] != "/"
  # tf.name_scope with a scope-name ending with "/" will interpret is as absolute name,
  # and use it as-is.
  # In all other cases, it would create a new name-scope with a new unique name,
  # which is not what we want.
  with tf.name_scope(name + "/"):
    # tf.name_scope will not set the variable scope.
    # tf.variable_scope will also set the name scope, but the logic is broken
    # for absolute name scopes, thus we had to do the tf.name_scope manually above.
    # We create the dummy_var_scope to force it to reuse that name.
    # Note that the reuse-argument might be miss-leading in this context:
    # It means that tf.get_variable() will search for existing variables and errors otherwise.
    dummy_var_scope = tf.VariableScope(reuse=None, name=name + "/")
    with tf.variable_scope(dummy_var_scope) as scope:
      assert isinstance(scope, tf.VariableScope)
      # remove "/" from the end of the var-scope.
      # This is a work-around to fix up the variable scope behavior for nested variable scopes.
      # Warning: This might break at some future point.
      assert scope.name is scope._name
      assert scope.name[-1] == "/"
      scope._name = scope._name[:-1]
      assert name == scope.name, "%r" % current_name_scope
      yield scope


@contextlib.contextmanager
def var_creation_scope():
  """
  If you create a variable inside of a while-loop, you might get the following error:
    InvalidArgumentError: The node 'while/w/Assign' has inputs from different frames.
    The input 'while/j' is in frame 'while/while/'. The input 'while/w' is in frame ''.
  Also see tests/test_TFUtil.py:test_loop_var_creation().
  Related TF bugs:
    https://github.com/tensorflow/tensorflow/issues/3114
    https://github.com/tensorflow/tensorflow/issues/4478
    https://github.com/tensorflow/tensorflow/issues/8604
  The solution is to reset the current frame.
  Resetting all control dependencies has this effect.
  """
  with tf.control_dependencies(None) as dep:
    yield dep


class FlipGradientBuilder(object):
  """
  Gradient Reversal Layer.
  Discussion:
      https://github.com/fchollet/keras/issues/3119
      https://github.com/tensorflow/tensorflow/issues/4342
  Code from here:
      https://github.com/pumpikano/tf-dann/blob/master/flip_gradient.py
  """

  def __init__(self):
    self.num_calls = 0

  def __call__(self, x, l=1.0):
    grad_name = "FlipGradient%d" % self.num_calls

    from tensorflow.python.framework import ops
    @ops.RegisterGradient(grad_name)
    def _flip_gradients(op, grad):
      return [tf.neg(grad) * l]

    g = tf.get_default_graph()
    with g.gradient_override_map({"Identity": grad_name}):
      y = tf.identity(x)

    self.num_calls += 1
    return y

flip_gradient = FlipGradientBuilder()


def check_input_ndim(x, ndim):
  """
  :param tf.Tensor x:
  :param int ndim:
  :return: x with check added
  :rtype: tf.Tensor
  """
  dyn_shape = x.get_shape()
  if dyn_shape.ndims is not None:
    assert dyn_shape.ndims == ndim
    return x
  # Need to fall-back to runtime check.
  with reuse_name_scope("checks"):
    with tf.control_dependencies(
      [tf.assert_equal(tf.rank(x), ndim, data=["ndim not %i" % ndim, tf.shape(x)])]):
      return tf.identity(x, "identity_with_ndim_check")


def check_input_ndim_equal_offset(x, y, y_ndim_offset=0):
  """
  :param tf.Tensor x:
  :param tf.Tensor y:
  :param int y_ndim_offset:
  :return: x with check added such that ndim(x) == ndim(y) + y_ndim_offset
  :rtype: tf.Tensor
  """
  x_dyn_shape = x.get_shape()
  y_dyn_shape = y.get_shape()
  if x_dyn_shape.ndims is not None and y_dyn_shape.ndims is not None:
    assert x_dyn_shape.ndims == y_dyn_shape.ndims + y_ndim_offset
    return x
  # Need to fall-back to runtime check.
  with reuse_name_scope("checks"):
    with tf.control_dependencies(
      [tf.assert_equal(tf.rank(x), tf.rank(y) + y_ndim_offset,
                       data=["ndim not equal with offset %i" % y_ndim_offset,
                             tf.shape(x), tf.shape(y)])]):
      return tf.identity(x, "identity_with_ndim_equal_check")


def check_input_dim(x, axis, dim):
  """
  :param tf.Tensor x:
  :param int axis: which axis to check
  :param int|tf.Tensor dim:
  :return: x with check added
  :rtype: tf.Tensor
  """
  dyn_shape = x.get_shape()
  if dyn_shape.ndims is not None and isinstance(dim, int):
    if dyn_shape.dims[axis].value is not None:
      assert dyn_shape.dims[axis].value == dim
      return x
  # Need to fall-back to runtime check.
  with reuse_name_scope("checks"):
    with tf.control_dependencies(
      [tf.assert_equal(tf.shape(x)[axis], dim, data=["shape[%i] not dim" % (axis,), dim, tf.shape(x)])]):
      return tf.identity(x, "identity_with_dim_check")


def check_dim_equal(x, x_axis, y, y_axis):
  """
  :param tf.Tensor x:
  :param int x_axis: which axis to check
  :param tf.Tensor y:
  :param int y_axis: which axis to check
  :return: x with check added that shape(x)[x_axis] == shape(y)[y_axis]
  :rtype: tf.Tensor
  """
  x_dyn_shape = x.get_shape()
  y_dyn_shape = y.get_shape()
  if x_dyn_shape.ndims is not None and y_dyn_shape.ndims is not None:
    if x_dyn_shape.dims[x_axis].value is not None and y_dyn_shape.dims[y_axis].value is not None:
      assert x_dyn_shape.dims[x_axis].value == y_dyn_shape.dims[y_axis].value
      return x
  # Need to fall-back to runtime check.
  with reuse_name_scope("checks"):
    with tf.control_dependencies(
      [tf.assert_equal(
        tf.shape(x)[x_axis], tf.shape(y)[y_axis],
        data=["x.shape[%i] not y.shape[%i]" % (x_axis, y_axis),
              tf.shape(x), tf.shape(y)])]):
      return tf.identity(x, "identity_with_dim_equal_check")


def check_shape_equal(x, y):
  """
  :param tf.Tensor x:
  :param tf.Tensor y:
  :return: x with check added that shape(x) == shape(y)
  :rtype: tf.Tensor
  """
  x_dyn_shape = x.get_shape()
  y_dyn_shape = y.get_shape()
  if x_dyn_shape.ndims is not None and y_dyn_shape.ndims is not None:
    assert x_dyn_shape.ndims == y_dyn_shape.ndims
    have_unknown = False
    for axis in range(x_dyn_shape.ndims):
      if x_dyn_shape.dims[axis].value is not None and y_dyn_shape.dims[axis].value is not None:
        assert x_dyn_shape.dims[axis].value == y_dyn_shape.dims[axis].value
      else:
        have_unknown = True
    if not have_unknown:
      return x  # all dims are checked, we can return
  # We need to fall-back to runtime check.
  with reuse_name_scope("checks"):
    with tf.control_dependencies(
      [tf.assert_equal(
        tf.shape(x), tf.shape(y),
        data=["x.shape not y.shape",
              tf.shape(x), tf.shape(y)])]):
      return tf.identity(x, "identity_with_shape_equal_check")


def get_shape_dim(x, axis, name="shape_dim"):
  """
  :param tf.Tensor x:
  :param int axis: which axis
  :param str name:
  :return: x.shape[axis] either as a static int or otherwise as an expression
  :rtype: int|tf.Tensor
  """
  dyn_shape = x.get_shape()
  if dyn_shape.ndims is not None:
    if dyn_shape.dims[axis].value is not None:
      return dyn_shape.dims[axis].value
  # Need to fall-back to runtime.
  with tf.name_scope(name):
    return tf.shape(x)[axis]


def get_shape(x):
  """
  :param tf.Tensor x:
  :return: list of scalars, which are either int if known statically, or otherwise expressions
  :rtype: list[int|tf.Tensor]
  """
  with tf.name_scope("get_shape"):
    dyn_shape = tf.shape(x)
    static_shape = x.get_shape()
    assert static_shape.ndims is not None
    return [static_shape.dims[i].value
            if static_shape.dims[i].value is not None
            else dyn_shape[i]
            for i in range(static_shape.ndims)]


def get_ndim(x):
  """
  :param tf.Tensor x:
  :return: x.ndim either as a static int or otherwise as an expression
  :rtype: int|tf.Tensor
  """
  dyn_shape = x.get_shape()
  if dyn_shape.ndims is not None:
    return dyn_shape.ndims
  # Need to fall-back to runtime.
  return tf.rank(x)


def get_range(start, stop=NotSpecified):
  """
  :param int|tf.Tensor|None start:
  :param int|tf.Tensor|None stop:
  :return: either tuple(range(start, stop)) or the same as a symbolic expression
  :rtype: tuple[int]|tf.Tensor
  """
  if stop is NotSpecified:
    stop = start
    start = 0
  if isinstance(start, tf.Tensor) or isinstance(stop, tf.Tensor):
    return tf.range(start, stop)
  return tuple(range(start, stop))


def identity_with_ops(x, ops):
  """
  :param tf.Tensor x:
  :param () -> list[tf.Operation|tf.Tensor] ops:
  :return: x with all ops executed
  :rtype: tf.Tensor
  """
  with reuse_name_scope("checks"):
    with tf.control_dependencies(ops()):
      return tf.identity(x, name="identity_with_ops")


def _guess_requested_max_num_threads(log_file=None):
  from Util import read_sge_num_procs
  try:
    sge_num_procs = read_sge_num_procs()
  except Exception as exc:
    if log_file:
      print("Error while getting SGE num_proc: %r" % exc, file=log_file)
  else:
    if sge_num_procs:
      if log_file:
        print("Use num_threads=%i (but min 2) via SGE num_proc." % sge_num_procs, file=log_file)
      return max(sge_num_procs, 2)
  omp_num_threads = int(os.environ.get("OMP_NUM_THREADS") or 0)
  if omp_num_threads:
    # Minimum of 2 threads, should not hurt.
    if log_file:
      print("Use num_threads=%i (but min 2) via OMP_NUM_THREADS." % omp_num_threads, file=log_file)
    return max(omp_num_threads, 2)
  return None


_setup_tf_thread_pools_called_once = False

def setup_tf_thread_pools(num_threads=None, log_file=None):
  """
  See here for documentation of intra_op_parallelism_threads and inter_op_parallelism_threads:
  https://github.com/tensorflow/tensorflow/blob/master/tensorflow/core/protobuf/config.proto

  intra_op_parallelism_threads is used for the LocalDevice::EigenThreadPoolInfo, which is always global.
  https://github.com/tensorflow/tensorflow/blob/master/tensorflow/core/common_runtime/local_device.cc

  inter_op_parallelism_threads is used for the (global if not use_per_session_threads) session thread pool.
  https://github.com/tensorflow/tensorflow/blob/master/tensorflow/core/common_runtime/direct_session.cc

  TF will setup the thread pools on first usage. That can happen quite early, esp for intra_op_parallelism_threads.
  E.g. list_local_devices() will trigger this, i.e. any call to is_gpu_available() or print_available_devices().
  For debugging, you can set the env-var TF_CPP_MIN_VLOG_LEVEL=1 and then check for these message:

      Local device intra op parallelism threads: 4
      Direct session inter op parallelism threads: 4

  Thus, call this function as early as possible with your preferred number of threads,
  used for both thread pools.
  It will create a dummy session and directly close it again, but if you use the global thread pools,
  those settings will remain for further sessions.
  This function will only execute on the first call.

  :param int num_threads: used for both intra and inter parallelism thread pools
  :param stream|None log_file:
  """
  global _setup_tf_thread_pools_called_once
  if _setup_tf_thread_pools_called_once:
    return
  _setup_tf_thread_pools_called_once = True
  if not num_threads:
    num_threads = _guess_requested_max_num_threads()
  if log_file:
    print("Setup TF inter and intra global thread pools with num_threads=%r." % num_threads, file=log_file)
  opts = {}
  opts.setdefault("log_device_placement", False)
  opts.setdefault("device_count", {}).setdefault("GPU", 0)
  if num_threads:
    opts.setdefault("intra_op_parallelism_threads", num_threads)
    opts.setdefault("inter_op_parallelism_threads", num_threads)
  with tf.Session(config=tf.ConfigProto(**opts)):
    pass


_list_local_devices = None

def _get_tf_list_local_devices():
  global _list_local_devices
  if _list_local_devices:
    return _list_local_devices
  print("Collecting TensorFlow device list...")
  _list_local_devices = list(device_lib.list_local_devices())
  return _list_local_devices


def _parse_physical_device_desc(s):
  """
  :param str s: string via dev.physical_device_desc. e.g. "device: 0, name: GeForce GTX 980, pci bus id: 0000:41:00.0"
  :return: dict key -> value
  :rtype: dict[str,str]
  """
  d = {}
  for part in s.split(","):
    part = part.strip()
    key, value = part.split(":", 1)
    key, value = key.strip(), value.strip()
    d[key] = value
  return d


def print_available_devices():
  cuda_visible_devs = None
  if "CUDA_VISIBLE_DEVICES" in os.environ:
    print("CUDA_VISIBLE_DEVICES is set to %r." % os.environ["CUDA_VISIBLE_DEVICES"])
    cuda_visible_devs = dict(enumerate([int(d) for d in os.environ["CUDA_VISIBLE_DEVICES"].split(",") if d]))
  else:
    print("CUDA_VISIBLE_DEVICES is not set.")
  devs = _get_tf_list_local_devices()
  print("Local devices available to TensorFlow:")
  for i, dev in enumerate(devs):
    print("  %i/%i: %s" % (i + 1, len(devs), "\n       ".join(str(dev).splitlines())))

  # Theano prints sth like: Using gpu device 2: GeForce GTX 980 (...)
  # Print in a similar format so that some scripts which grep our stdout work just as before.
  for dev in devs:
    if dev.device_type == "GPU":
      d = _parse_physical_device_desc(dev.physical_device_desc)
      dev_id = int(d["device"])
      if cuda_visible_devs:
        dev_id = cuda_visible_devs[dev_id]
      dev_name = d["name"]
      print("Using gpu device %i: %s" % (dev_id, dev_name))


def is_gpu_available():
  """Returns whether TensorFlow can access a GPU."""
  return any(x.device_type == 'GPU' for x in _get_tf_list_local_devices())


def dot(a, b):
  """
  :param tf.Tensor a: shape [...da...,d]
  :param tf.Tensor b: shape [d,...db...]
  :return: tensor of shape [...da...,d,...db...]
  :rtype: tf.Tensor
  """
  with tf.name_scope("dot"):
    a_ndim = a.get_shape().ndims
    b_ndim = b.get_shape().ndims
    assert a_ndim is not None
    if a_ndim == 0:
      return tf.scalar_mul(a, b)
    assert b_ndim is not None
    if b_ndim == 0:
      return tf.scalar_mul(b, a)
    a = check_dim_equal(a, -1, b, 0)
    if a_ndim == b_ndim == 1:
      return tf.reduce_sum(a * b)
    a_shape = tf.shape(a)
    b_shape = tf.shape(b)
    d = get_shape_dim(b, 0)
    assert a_ndim >= 2 and b_ndim >= 2
    if a_ndim > 2:
      a = tf.reshape(a, (-1, d))
    if b_ndim > 2:
      b = tf.reshape(b, (d, -1))
    res = tf.matmul(a, b)
    if a_ndim > 2 or b_ndim > 2:
      res = tf.reshape(
        res, [a_shape[i] for i in range(0, a_ndim - 1)] + [b_shape[i] for i in range(1, b_ndim)])
    return res


def identity(x):
  """
  :param tf.Tensor x:
  :rtype: tf.Tensor
  """
  return x


def _plus(a, b):
  return a + b


def _minus(a, b):
  return a - b


def _mul(a, b):
  return a * b


def _div(a, b):
  return a / b


_bin_ops = {"+": _plus, "-": _minus, "*": _mul, "/": _div}


def _get_act_func_with_op(s):
  """
  :param str s: e.g. "2 * sigmoid" or even "3 + 2 * sigmoid"
  :rtype: (tf.Tensor) -> tf.Tensor
  """
  def _conv(v):
    v = v.strip()
    from Util import str_is_number
    if str_is_number(v):
      try:
        v = int(v)
      except ValueError:
        v = float(v)
      return lambda x: v
    else:
      return get_activation_function(v)
  a, b = None, None
  for k in _bin_ops:
    if k in s:
      a, b = s.split(k, 2)
      a, b = _conv(a), _conv(b)
      def combined_op(x):
        return _bin_ops[k](a(x), b(x))
      return combined_op
  assert False


def get_activation_function(s):
  """
  :param str|None s:
  :rtype: (tf.Tensor) -> tf.Tensor
  """
  if not s or s in ["none", "identity"]:
    return identity
  if any(k in s for k in _bin_ops):
    return _get_act_func_with_op(s)
  act_func = getattr(tf.nn, s)  # e.g. relu, elu, sigmoid, softmax, ...
  return act_func


def swapaxes(x, axis1, axis2):
  """
  :param tf.Tensor x:
  :param tf.Tensor|int axis1:
  :param tf.Tensor|int axis2:
  :return: tensor with swapped axes, like numpy.swapaxes
  :rtype: tf.Tensor
  """
  with tf.name_scope("swapaxes"):
    ndim = x.get_shape().ndims
    if ndim is not None:
      if isinstance(axis1, tf.Tensor) or isinstance(axis2, tf.Tensor):
        perm = [tf.where(tf.equal(axis1, i), axis2,
                         tf.where(tf.equal(axis2, i), axis1,
                                  i))
                for i in range(ndim)]
      else:
        perm = list(range(ndim))
        perm[axis1] = axis2
        perm[axis2] = axis1
    else:
      # Just fall back to the very generic pure symbolic variant.
      rank = tf.rank(x)
      all_axes = tf.range(rank)
      assert all_axes.get_shape().ndims == 1
      axis1 = tf.convert_to_tensor(axis1)
      axis2 = tf.convert_to_tensor(axis2)
      assert axis1.get_shape().ndims == 0
      assert axis2.get_shape().ndims == 0
      axis1_bc = tf.expand_dims(axis1, 0)
      axis2_bc = tf.expand_dims(axis2, 0)
      perm = tf.where(tf.equal(axis1_bc, all_axes), axis2_bc,
                      tf.where(tf.equal(axis2_bc, all_axes), axis1_bc,
                               all_axes))
    return tf.transpose(x, perm=perm)


def move_axis(x, old_axis, new_axis):
  """
  :param tf.Tensor x:
  :param int old_axis:
  :param int new_axis:
  """
  with tf.name_scope("move_axis"):
    ndim = x.get_shape().ndims
    assert ndim is not None, "not supported currently: %r" % x
    perm = list(range(ndim))
    old = perm.pop(old_axis)
    perm.insert(new_axis, old)
    return tf.transpose(x, perm)


def sequence_mask(lengths, **kwargs):
  """
  Wraps around tf.sequence_mask().
  It will cache the value inside the passed object so that we don't recompute it multiple times.

  :param tf.Tensor lengths: shape (batch,)
  :param dict[str] kwargs: passed on to tf.sequence_mask
  :return: tensor mask of shape (batch,maxlen/time). default dtype is bool unless you specify something else
  :rtype: tf.Tensor
  """
  if hasattr(lengths, "_sequence_mask"):
    return lengths._sequence_mask
  mask = tf.sequence_mask(lengths, **kwargs)
  lengths._sequence_mask = mask
  return mask


def sequence_mask_time_major(lengths, **kwargs):
  """
  Wraps around tf.transpose(tf.sequence_mask(), (1,0)).
  It will cache the value inside the passed object so that we don't recompute it multiple times.

  :param tf.Tensor lengths: shape (batch,)
  :param dict[str] kwargs: passed on to tf.sequence_mask
  :return: mask of shape (maxlen/time,batch)
  """
  if hasattr(lengths, "_sequence_mask_time_major"):
    return lengths._sequence_mask_time_major
  mask = sequence_mask(lengths=lengths, **kwargs)  # shape (time,batch)
  mask = tf.transpose(mask, (1, 0))  # shape (batch,time)
  lengths._sequence_mask_time_major = mask
  return mask


def directed(x, direction):
  """
  If direction == 1 or direction is None, returns just x.
  If direction == -1, returns reversed(x).

  :param tf.Tensor x:
  :param int|None direction: -1 or 1 (or None)
  :rtype: tf.Tensor
  """
  if direction == 1 or direction is None:
    return x
  if direction == -1:
    return reversed(x)
  raise ValueError("invalid direction: %r" % direction)


def reversed(x):
  """
  Just returns x[::-1].
  It will cache the value inside the passed object so that we don't recompute it multiple times.

  :param tf.Tensor x:
  :rtype: tf.Tensor
  """
  if hasattr(x, "_reversed_dim0"):
    return x._reversed_dim0
  y = x[::-1]
  x._reversed_dim0 = y
  y._reversed_dim0 = x
  return y


def flatten_with_seq_len_mask(x, seq_lens, time_major=False):
  """
  :param tf.Tensor x: shape (batch,time,...s...) with time_major=False or otherwise shape (time,batch,...s....)
  :param tf.Tensor seq_lens: shape (batch,) of int32
  :param bool time_major: if the time-dim is the first dimension in x
  :return: tensor of shape (time', ...s...) where time' = sum(seq_len) <= batch*time
  :rtype: tf.Tensor
  """
  with tf.name_scope("flatten_with_seq_len_mask"):
    seq_lens = check_input_ndim(seq_lens, 1)
    if time_major:
      x = swapaxes(x, 0, 1)  # get (batch,time,...s...)
    x = check_dim_equal(x, 0, seq_lens, 0)  # batch dim
    # int64? -> https://github.com/tensorflow/tensorflow/issues/6518
    mask = sequence_mask(seq_lens, maxlen=tf.shape(x)[1])  # shape (batch,time)
    mask = check_input_ndim(mask, 2)
    mask = check_dim_equal(mask, 0, x, 0)
    mask = check_dim_equal(mask, 1, x, 1)
    res = tf.boolean_mask(x, mask)
    res = check_input_ndim_equal_offset(res, x, -1)
    return res


def expand_dims_unbroadcast(x, axis, dim, name="expand_dims_unbroadcast"):
  """
  :param tf.Tensor x:
  :param int|tf.Tensor axis: new axis
  :param int|tf.Tensor dim: dimension for axis
  :param str name: scope name
  :return: if x is of shape (a,b,c) and axis=0, then we return (dim,a,b,c)
  :rtype: tf.Tensor
  """
  with tf.name_scope(name):
    x = tf.expand_dims(x, axis)
    if dim is not 1:
      new_ndim = x.get_shape().ndims
      assert new_ndim is not None, "not implemented otherwise yet"
      assert isinstance(axis, int), "not implemented otherwise yet"
      x = tf.tile(x, [dim if (axis == i) else 1 for i in range(new_ndim)])
    return x


def expand_multiple_dims(x, axes, name="expand_multiple_dims"):
  """
  :param tf.Tensor x:
  :param list[int]|tuple[int] axes: after completion, tf.shape(y)[axis] == 1 for axis in axes
  :param str name: scope name
  :return: y where we have a new broadcast axis for each axis in axes
  :rtype: tf.Tensor
  """
  with tf.name_scope(name):
    for i in sorted(axes):
      x = tf.expand_dims(x, axis=i, name="expand_axis_%i" % i)
    return x


def tile_transposed(x, axis, multiples):
  """
  Example: x with shape (D,), tf.tile(x, [N]) can be reshaped into (N,D),
  while tile_transposed(x, axis=0, multiples=N) can be reshaped into (D,N).

  :param tf.Tensor x:
  :param int axis:
  :param int|tf.Tensor multiples:
  :return: tensor with shape[axis] == x.shape[axis] * multiples
  :rtype: tf.Tensor
  """
  with tf.name_scope("tile_transposed"):
    ndim = x.get_shape().ndims
    assert ndim is not None
    shape = tf.shape(x)
    x = expand_dims_unbroadcast(x, axis=axis + 1, dim=multiples)  # new axis after `axis`
    return tf.reshape(
      x,
      [shape[i] for i in range(axis)] +
      [shape[axis] * multiples] +
      [shape[i] for i in range(axis + 1, ndim)])


def constant_with_shape(x, shape, dtype=None, name="constant_with_shape"):
  """
  :param tf.Tensor|float|int|bool x: scalar
  :param list[tf.Tensor|int]|tuple[tf.Tensor|int]|tf.Tensor shape:
  :param tf.DType dtype:
  :param str name:
  :return: x of the specified shape
  :rtype: tf.Tensor
  """
  with tf.name_scope(name):
    x = tf.convert_to_tensor(x, dtype=dtype)
    ones = tf.ones(shape, dtype=x.dtype)
    if x.dtype == tf.bool:
      return tf.logical_and(x, ones)
    return tf.multiply(x, ones)


def dimshuffle(x, axes, name="dimshuffle"):
  """
  Like Theanos dimshuffle.
  Combines tf.transpose, tf.expand_dims and tf.squeeze.

  :param tf.Tensor x:
  :param list[int|str]|tuple[int|str] axes:
  :param str name: scope name
  :rtype: tf.Tensor
  """
  with tf.name_scope(name):
    assert all([i == "x" or isinstance(i, int) for i in axes])
    real_axes = [i for i in axes if isinstance(i, int)]
    bc_axes = [i for (i, j) in enumerate(axes) if j == "x"]
    if x.get_shape().ndims is None:
      x_shape = tf.shape(x)
      x = tf.reshape(x, [x_shape[i] for i in range(max(real_axes) + 1)])  # will have static ndims
    assert x.get_shape().ndims is not None

    # First squeeze missing axes.
    i = 0
    while i < x.get_shape().ndims:
      if i not in real_axes:
        x = tf.squeeze(x, axis=i)
        real_axes = [(j if (j < i) else (j - 1)) for j in real_axes]
      else:
        i += 1

    # Now permute.
    assert list(sorted(real_axes)) == list(range(x.get_shape().ndims))
    if real_axes != list(range(x.get_shape().ndims)):
      x = tf.transpose(x, real_axes)

    # Now add broadcast dimensions.
    if bc_axes:
      x = expand_multiple_dims(x, bc_axes)
    assert len(axes) == x.get_shape().ndims
    return x


def sparse_labels_with_seq_lens(x, seq_lens, dtype=tf.int32, collapse_repeated=False):
  """
  :param tf.Tensor x: shape (batch,time) -> index, some int type
  :param tf.Tensor|None seq_lens: shape (batch,) of int32|int64
  :param tf.DType|None dtype: if given, will cast the `x` values to this type. ctc_loss() wants int32
  :param bool collapse_repeated: like uniq() behavior
  :return: SparseTensor, e.g. input for tf.nn.ctc_loss(), and seq_lens of shape (batch,)
  :rtype: (tf.SparseTensor, tf.Tensor)
  """
  with tf.name_scope("sparse_labels"):
    x = check_input_ndim(x, ndim=2)
    if seq_lens is not None:
      x = check_dim_equal(x, 0, seq_lens, 0)
    if dtype:
      x = tf.cast(x, dtype)
    batch_size = tf.shape(x)[0]
    max_time = tf.shape(x)[1]
    if seq_lens is not None:
      mask = sequence_mask(seq_lens, maxlen=max_time)  # shape (batch,time)
    else:
      mask = tf.ones(dtype=tf.bool, shape=(batch_size, max_time))
    if collapse_repeated:
      with tf.name_scope("collapse_repeated"):
        diffs = tf.concat(
          1, [tf.ones_like(x[:, :1], dtype=tf.bool), tf.not_equal(x[:, 1:], x[:, :-1])])  # shape (batch,time)
        mask = tf.logical_and(diffs, mask)
    with tf.name_scope("flat_x"):
      flat_x = tf.boolean_mask(x, mask)  # (N, ...s...)
    with tf.name_scope("idxs"):
      if collapse_repeated:
        # Recalculate mask, so that we have them all behind each other.
        seq_lens = tf.reduce_sum(tf.cast(mask, tf.int32), axis=1)
        max_time = tf.reduce_max(seq_lens)
        mask = sequence_mask(seq_lens)
      time_idxs = expand_dims_unbroadcast(tf.range(max_time), 0, batch_size)  # shape (batch,time)
      flat_time_idxs = tf.boolean_mask(time_idxs, mask)  # (N,)
      batch_idxs = expand_dims_unbroadcast(tf.range(batch_size), 1, max_time)  # shape (batch,time)
      flat_batch_idxs = tf.boolean_mask(batch_idxs, mask)  # (N,)
      flat_idxs = tf.stack([flat_batch_idxs, flat_time_idxs], axis=1)  # shape (N, 2)
      # tf.SparseTensor requires int64 indices
      flat_idxs = tf.cast(flat_idxs, tf.int64)
    with tf.name_scope("shape"):
      shape = [batch_size, max_time]
      # tf.SparseTensor requires int64 shape
      shape = [tf.cast(d, tf.int64) for d in shape]
      shape = tf.convert_to_tensor(shape)
    # tf.SparseTensor args:
    #   indices: A 2-D int64 tensor of shape `[N, ndims]`.
    #   values: A 1-D tensor of any type and shape `[N]`.
    #   shape: A 1-D int64 tensor of shape `[ndims]`.
    return tf.SparseTensor(flat_idxs, flat_x, shape), seq_lens


def sparse_labels(x, seq_lens, dtype=tf.int32, collapse_repeated=False):
  """
  :param tf.Tensor x: shape (batch,time) -> index, some int type
  :param tf.Tensor|None seq_lens: shape (batch,) of int32|int64
  :param tf.DType|None dtype: if given, will cast the `x` values to this type. ctc_loss() wants int32
  :param bool collapse_repeated: like uniq() behavior
  :return: SparseTensor, e.g. input for tf.nn.ctc_loss()
  :rtype: tf.SparseTensor
  """
  y, _ = sparse_labels_with_seq_lens(x=x, seq_lens=seq_lens, dtype=dtype, collapse_repeated=collapse_repeated)
  return y


def uniq(x):
  """
  :param tf.Tensor x: 1D shape (time,) -> index, some int type
  :return: like numpy.uniq. unlike tf.unique which will never repeat entries.
  Example: uniq([0, 0, 1, 1, 0, 0]) == [0, 1, 0], tf.unique([0, 0, 1, 1, 0, 0]) == [0, 1].
  For a batched variant, see batched_uniq, or sparse_labels() with option collapse_repeated.
  """
  diffs = tf.concat(0, [tf.ones_like(x[:1]), x[1:] - x[:-1]])
  nonzero_idx = tf.where(diffs)
  x_uniq = tf.gather_nd(x, nonzero_idx)
  return x_uniq


def batched_uniq(x, seq_lens):
  """
  :param tf.Tensor x: shape (batch,time) -> index, some int type
  :param tf.Tensor|None seq_lens: shape (batch,) of int32|int64
  :return: tuple (z, new_seq_lens), where z is of shape (batch, max_new_time),
    max_new_time = max(new_seq_lens), seq_lens is of shape (batch,).
  :rtype: (tf.Tensor, tf.Tensor)
  """
  y, new_seq_lens = sparse_labels_with_seq_lens(x, seq_lens=seq_lens, collapse_repeated=True)
  z = tf.sparse_to_dense(sparse_indices=y.indices, sparse_values=y.values, output_shape=y.dense_shape)
  return z, new_seq_lens


class VariableAssigner(object):
  def __init__(self, var):
    """
    :param tf.Variable var:
    """
    self.var = var
    self.value_placeholder = tf.placeholder(
      name="%s_placeholder_assign_value" % var.name.split("/")[-1][:-2],
      shape=var.get_shape(),
      dtype=var.dtype)
    self.assign_op = tf.assign(self.var, self.value_placeholder)

  def assign(self, value, session):
    """
    :param numpy.ndarray|int|float value:
    :param tf.Session session:
    """
    session.run(self.assign_op, feed_dict={self.value_placeholder: value})


class CudaEnv(object):
  _instance = None
  verbose_find_cuda = False

  def __init__(self):
    self.cuda_path = self._find_cuda_path()

  @classmethod
  def _find_nvcc_in_path(cls):
    """
    :return: yields full path to nvcc
    :rtype: list[str]
    """
    for p in os.environ["PATH"].split(":"):
      pp = "%s/nvcc" % p
      if os.path.exists(pp):
        yield pp

  @classmethod
  def _find_lib_in_ld_path(cls):
    """
    :return: yields full path to libcudart.so
    :rtype: list[str]
    """
    if not os.environ.get("LD_LIBRARY_PATH"):
      return
    for p in os.environ["LD_LIBRARY_PATH"].split(":"):
      pp = "%s/libcudart.so" % p
      if os.path.exists(pp):
        yield pp

  @classmethod
  def _get_lib_dir_name(cls):
    from Util import is_64bit_platform
    if is_64bit_platform():
      return "lib64"
    return "lib"

  @classmethod
  def _cuda_path_candidates(cls):
    for p in cls._find_nvcc_in_path():
      # Expect p == "/usr/local/cuda-8.0/bin/nvcc" or so.
      postfix = "/bin/nvcc"
      if cls.verbose_find_cuda:
        print("found cuda nvcc (wanted postfix: %r): %s" % (postfix, p))
      if not p.endswith(postfix):
        continue
      yield p[:-len(postfix)]
    for p in cls._find_lib_in_ld_path():
      # Expect p == "/usr/local/cuda-8.0/lib64/libcudart.so" or so.
      postfix = "/%s/libcudart.so" % cls._get_lib_dir_name()
      if cls.verbose_find_cuda:
        print("found cuda lib (wanted postfix: %r): %s" % (postfix, p))
      if not p.endswith(postfix):
        continue
      yield p[:-len(postfix)]

  @classmethod
  def _check_valid_cuda_path(cls, p):
    """
    :param str p: path to CUDA, e.g. "/usr/local/cuda-8.0"
    :return: whether this is a valid CUDA path, i.e. we find all what we need
    :rtype: bool
    """
    if cls.verbose_find_cuda:
      print("check valid CUDA path: %s" % p)
    if not os.path.exists("%s/bin/nvcc" % p):
      return False
    if not os.path.exists("%s/include/cuda.h" % p):
      return False
    if not os.path.exists("%s/%s/libcudart.so" % (p, cls._get_lib_dir_name())):
      return False
    return True

  @classmethod
  def _find_cuda_path(cls):
    """
    :return: base CUDA path if we find one, otherwise None
    :rtype: str|None
    """
    for p in cls._cuda_path_candidates():
      if cls._check_valid_cuda_path(p):
        return p
    return None

  def is_available(self):
    return bool(self.cuda_path)

  def get_compiler_opts(self):
    return [
      "-I", "%s/include" % self.cuda_path, "-L", "%s/%s" % (self.cuda_path, self._get_lib_dir_name()),
      "-x", "cu"]

  def get_compiler_bin(self):
    assert self.cuda_path
    return "%s/bin/nvcc" % self.cuda_path

  @classmethod
  def get_instance(cls):
    """
    :rtype: CudaEnv
    """
    if cls._instance is not None:
      return cls._instance
    cls._instance = cls()
    return cls._instance


class OpCodeCompiler(object):
  """
  Helper class to compile TF ops on-the-fly, similar as Theano.
  https://www.tensorflow.org/versions/master/how_tos/adding_an_op/
  """

  def __init__(self, base_name, code_version, code, c_macro_defines=None, ld_flags=None, include_deps=None,
               static_version_name=None, should_cleanup_old_all=True, should_cleanup_old_mydir=False,
               use_cuda_if_available=True):
    """
    :param str base_name: base name for the module, e.g. "zero_out"
    :param int|tuple[int] code_version: check for the cache whether to reuse
    :param str code: the source code itself
    :param dict[str,str|int]|None c_macro_defines: e.g. {"TENSORFLOW": 1}
    :param list[str]|None ld_flags: e.g. ["-lblas"]
    :param list[str]|None include_deps: if provided and an existing lib file, we will check if any dependency is newer
      and we need to recompile. we could also do it automatically via -MD but that seems overkill and too slow.
    :param str|None static_version_name: normally, we use .../base_name/hash as the dir
      but this would use .../base_name/static_version_name.
    :param bool should_cleanup_old_all: whether we should look in the cache dir
      and check all ops if we can delete some old ones which are older than some limit (self._cleanup_time_limit_days)
    :param bool should_cleanup_old_mydir: whether we should delete our op dir before we compile there.
    """
    from Util import get_temp_dir
    self.cache_dir = "%s/returnn_tf_cache" % get_temp_dir()
    self._include_path = tf.sysconfig.get_include()  # e.g. "...python2.7/site-packages/tensorflow/include"
    self.base_name = base_name
    self.code_version = code_version
    self.code = code
    self.c_macro_defines = c_macro_defines or {}
    self.ld_flags = ld_flags or []
    self.include_deps = include_deps
    self.static_version_name = static_version_name
    self._cuda_env = use_cuda_if_available and CudaEnv.get_instance()
    self._code_hash = self._make_code_hash()
    self._info_dict = self._make_info_dict()
    self._hash = self._make_hash()
    self._mod = None
    if should_cleanup_old_all:
      self._cleanup_old()
    self._should_cleanup_old_mydir = should_cleanup_old_mydir

  @property
  def _mod_path(self):
    return "%s/ops/%s/%s" % (self.cache_dir, self.base_name, self.static_version_name or self._hash[:10])

  @property
  def _info_filename(self):
    return "%s/info.py" % (self._mod_path,)

  @property
  def _so_filename(self):
    return "%s/%s.so" % (self._mod_path, self.base_name)

  @property
  def _cc_filename(self):
    return "%s/%s.cc" % (self._mod_path, self.base_name)

  _cleanup_time_limit_days = 60

  def _cleanup_old(self):
    mod_path = self._mod_path  # .../base_name/hash
    base_mod_path = os.path.dirname(mod_path)  # .../base_name
    my_mod_path_name = os.path.basename(mod_path)
    if not os.path.exists(base_mod_path):
      return
    import time
    from Util import hms, LockFile
    cleanup_time_limit_secs = self._cleanup_time_limit_days * 24 * 60 * 60
    for p in os.listdir(base_mod_path):
      if p == my_mod_path_name:
        continue
      full_dir_path = "%s/%s" % (base_mod_path, p)
      if not os.path.isdir(full_dir_path):
        continue  # ignore for now
      lock = LockFile(full_dir_path)
      if lock.is_locked():
        continue
      lock.maybe_remove_old_lockfile()
      info_path = "%s/info.py" % full_dir_path
      if not os.path.exists(info_path):
        self._cleanup_old_path(full_dir_path, reason="corrupt dir, missing info.py")
        continue
      so_path = "%s/%s.so" % (full_dir_path, self.base_name)
      if not os.path.exists(so_path):
        self._cleanup_old_path(full_dir_path, reason="corrupt dir, missing so")
        continue
      dt = time.time() - os.path.getmtime(so_path)
      if dt > cleanup_time_limit_secs:
        self._cleanup_old_path(full_dir_path, reason="%s old" % hms(dt))

  def _cleanup_old_path(self, p, reason):
    print("OpCompiler delete old, %s: %s" % (reason, p))
    assert os.path.exists(p)
    import shutil
    try:
      shutil.rmtree(p)
    except OSError as exc:
      print("OpCompiler delete exception (%s). Will ignore and try to continue anyway." % exc)

  def _load_info(self):
    filename = self._info_filename
    if not os.path.exists(filename):
      return None
    s = open(filename).read()
    return eval(s)

  _relevant_info_keys = ("tf_version", "code_version", "with_cuda", "code_hash", "c_macro_defines", "ld_flags")

  def _make_info_dict(self):
    return {
      "base_name": self.base_name,
      "tf_version": tf.__version__,
      "tf_include_path": self._include_path,
      "code_version": self.code_version,
      "code_hash": self._code_hash,
      "c_macro_defines": self.c_macro_defines,
      "ld_flags": self.ld_flags,
      "with_cuda": bool(self._cuda_env and self._cuda_env.is_available())
    }

  def _make_code_hash(self):
    import hashlib
    hash = hashlib.md5()
    hash.update(self.code.encode("utf8"))
    return hash.hexdigest()

  def _make_hash(self):
    import hashlib
    hash = hashlib.md5()
    hash.update("{".encode("utf8"))
    for key in self._relevant_info_keys:
      hash.update(("%s:{%s}" % (key, self._info_dict[key])).encode("utf8"))
    hash.update("}".encode("utf8"))
    return hash.hexdigest()

  def _save_info(self):
    filename = self._info_filename
    from Util import betterRepr
    with open(filename, "w") as f:
      f.write("%s\n" % betterRepr(self._info_dict))

  def _need_recompile(self):
    if not os.path.exists(self._so_filename):
      return True
    if self.include_deps:
      so_mtime = os.path.getmtime(self._so_filename)
      for fn in self.include_deps:
        if os.path.getmtime(fn) > so_mtime:
          return True
    old_info = self._load_info()
    new_info = self._make_info_dict()
    if not old_info:
      return True
    # The hash already matched but very unlikely, this could be a collision.
    # Anyway, just do this very cheap check.
    for key in self._relevant_info_keys:
      if key not in old_info:
        return True
      if old_info[key] != new_info[key]:
        return True
    # If no code version is provided, we could also check the code itself now.
    # But I think this is overkill.
    return False

  def _maybe_compile(self):
    if not self._need_recompile():
      # Touch it so that we can see that we used it recently.
      os.utime(self._info_filename, None)
      return
    from Util import LockFile
    lock = LockFile(self._mod_path)
    if self._should_cleanup_old_mydir and not lock.is_locked():
      if os.path.exists(self._mod_path):
        self._cleanup_old_path(self._mod_path, reason="need recompile")
    with lock:
      self._maybe_compile_inner()

  def _maybe_compile_inner(self):
    # Directory should be created by the locking mechanism.
    assert os.path.exists(self._mod_path)
    with open(self._cc_filename, "w") as f:
      f.write(self.code)
    common_opts = ["-shared", "-O2", "-std=c++11"]
    if sys.platform == "darwin":
      common_opts += ["-undefined", "dynamic_lookup"]
    common_opts += ["-I", self._include_path]
    compiler_opts = ["-fPIC"]
    if self._cuda_env and self._cuda_env.is_available():
      common_opts += self._cuda_env.get_compiler_opts()
      common_opts += ["-DGOOGLE_CUDA=1"]
      for opt in compiler_opts:
        common_opts += ["-Xcompiler", opt]
    else:
      common_opts += compiler_opts
    common_opts += ["-D_GLIBCXX_USE_CXX11_ABI=0"]  # might be obsolete in the future
    common_opts += ["-D%s=%s" % item for item in sorted(self.c_macro_defines)]
    opts = common_opts + [self._cc_filename, "-o", self._so_filename]
    opts += self.ld_flags
    cmd_bin = "g++"
    if self._cuda_env and self._cuda_env.is_available():
      cmd_bin = self._cuda_env.get_compiler_bin()
    cmd_args = [cmd_bin] + opts
    from subprocess import Popen, PIPE, STDOUT, CalledProcessError
    print("OpCompiler call: %s" % " ".join(cmd_args))
    proc = Popen(cmd_args, cwd=self._mod_path, stdout=PIPE, stderr=STDOUT)
    stdout, stderr = proc.communicate()
    assert stderr is None  # should only have stdout
    if proc.returncode != 0:
      print("OpCompiler: %s failed." % cmd_bin)
      print("Original stdout/stderr:")
      print(stdout)
      raise CalledProcessError(returncode=proc.returncode, cmd=cmd_args)
    assert os.path.exists(self._so_filename)
    self._save_info()
    assert not self._need_recompile()

  def load_module(self):
    if self._mod:
      return self._mod
    self._maybe_compile()
    self._mod = tf.load_op_library(self._so_filename)
    return self._mod


def make_var_tuple(v):
  """
  :param tf.Tensor|list[tf.Tensor]|tuple[tf.Tensor] v:
  :return: tuple of tensors
  :rtype: tuple[tf.Tensor]
  """
  if isinstance(v, (int, float, tf.Tensor, tf.Operation)):
    return (v,)
  if isinstance(v, list):
    return tuple(v)
  assert isinstance(v, tuple)
  return v


def add_scaled_noise_to_gradients(grads_and_vars, gradient_noise_scale):
  """
  Adds scaled noise from a 0-mean normal distribution to gradients.
  Adapted from tf.contrib.layers.optimizers.

  :param list[(tf.Tensor, tf.Variable)] grads_and_vars:
  :param float gradient_noise_scale: used as stddev for tf.truncated_normal().
  :return: adapted grads_and_vars
  :rtype: list[(tf.Tensor, tf.Variable)]
  """
  gradients, variables = zip(*grads_and_vars)
  noisy_gradients = []
  for gradient in gradients:
    if gradient is None:
      noisy_gradients.append(None)
      continue
    if isinstance(gradient, tf.IndexedSlices):
      gradient_shape = gradient.dense_shape
    else:
      gradient_shape = gradient.get_shape()
    noise = tf.truncated_normal(gradient_shape, stddev=gradient_noise_scale)
    noisy_gradients.append(gradient + noise)
  return list(zip(noisy_gradients, variables))


class CustomGradient(object):
  def __init__(self):
    self.num_calls = 0
    self.registered_ops = {}  # func -> decorated func

  def Defun(self, *input_types, **kwargs):
    """
    :param (tf.Operation, tf.Tensor) -> tf.Tensor grad_op:
    :param list[tf.DType] input_types:
    :param dict[str] kwargs: passed to self.register()
    :return: function decorator
    :rtype: ((tf.Tensor) -> tf.Tensor) -> ((tf.Tensor) -> tf.Tensor)
    """

    def decorator(op):
      return self.register(input_types=input_types, op=op, **kwargs)

    return decorator

  def register(self, input_types, op, grad_op, name=None):
    """
    :param list[tf.DType] input_types:
    :param (tf.Tensor) -> tf.Tensor op:
    :param (tf.Operation, tf.Tensor) -> tf.Tensor grad_op: args are (op, out_grad) and it must return in_grad
    :param str name: optional func_name
    :return: op
    :rtype: (tf.Tensor) -> tf.Tensor
    """
    if op in self.registered_ops:
      return self.registered_ops[op]
    from tensorflow.python.framework import function
    op_with_new_grad = function.Defun(*input_types, python_grad_func=grad_op, func_name=name)(op)
    self.registered_ops[op] = op_with_new_grad
    # We need to add one instance of the new op to the graph now because of:
    # https://github.com/tensorflow/tensorflow/issues/6804
    op_with_new_grad(*[tf.placeholder(dtype) for dtype in input_types])
    return op_with_new_grad


custom_gradient = CustomGradient()


def filter_grad(x, threshold, axis):
  """
  :param tf.Tensor x:
  :param float threshold: all grads going through `x` which max(grad**2) is over the threshold are removed
  :param int|list[int] axis: max(grad**2) will be reduced over this axis
  :return: identity(x) with custom gradient
  :rtype: tf.Tensor
  """
  def grad_op(op, out_grad):
    with tf.name_scope("filter_grad__grad_op"):
      assert isinstance(op, tf.Operation)
      assert isinstance(out_grad, tf.Tensor)
      out_grad.set_shape(op.inputs[0].get_shape())
      keep_filter = tf.less(tf.reduce_max(out_grad ** 2, axis=axis, keep_dims=True), threshold)
      # keep_filter must be the same shape as out_grad.
      keep_filter = tf.logical_and(keep_filter, tf.ones_like(out_grad, dtype=tf.bool))
      out_grad = tf.where(keep_filter, out_grad, tf.zeros_like(out_grad))
      return out_grad

  with tf.name_scope("filter_grad"):
    op = custom_gradient.register([x.dtype], op=identity, grad_op=grad_op)
    y = op(x)
    y.set_shape(x.get_shape())
    return y


def debugRegisterBetterRepr():
  """
  Some types don't have good __repr__ implementations by default (for the current TF version).
  For debugging, it can be helpful to give some more info.
  This monkey-patches clazz.__repr__ of some TF classes if they are object.__repr__.
  """

  from tensorflow.python.framework import tensor_util

  def indexed_slices_repr(x):
    """
    :param tf.IndexedSlices x:
    :rtype: str
    """
    dense_shape = tensor_util.constant_value_as_shape(x.dense_shape)
    return "<tf.IndexedSlices %r dense_shape=%r dtype=%r>" % (x.name, dense_shape, x.dtype)

  def op_repr(x):
    """
    :param tf.Operation x:
    :rtype: str
    """
    return "<tf.Operation %r type=%r inputs=%r>" % (x.name, x.type, list(x.inputs))

  def var_repr(x):
    """
    :param tf.Variable x:
    :rtype: str
    """
    return "<tf.Variable %r initial_value=%r>" % (x.name, x.initial_value)

  def tensorarray_repr(x):
    """
    :param tf.TensorArray x:
    :rtype: str
    """
    op = x.handle.op
    assert isinstance(op, tf.Operation)
    return "<tf.TensorArray %r>" % op.name

  for cl, f in [
        (tf.IndexedSlices, indexed_slices_repr),
        (tf.Operation, op_repr),
        (tf.Variable, var_repr),
        (tf.TensorArray, tensorarray_repr)]:
    if getattr(cl, "__repr__") is object.__repr__:
      setattr(cl, "__repr__", f)


def cond(pred, fn1, fn2, name=None):
  """
  This is a wrapper around tf.control_flow_ops.cond().
  This will be a branched execution, i.e. either fn1() or fn2() will be executed,
  or at least the resulting graph will be evaluated.
  If pred can is constant at the call, only the corresponding fn will be called.
  This is similar as the TF internal _smart_cond().

  :param tf.Tensor|bool pred:
  :param ()->(tf.Tensor|list[tf.Tensor]) fn1:
  :param ()->(tf.Tensor|list[tf.Tensor]) fn2:
  :param str name:
  :return: fn1() if pred else fn2()
  :rtype: tf.Tensor|list[tf.Tensor]
  """
  if not callable(fn1):
    raise TypeError("fn1 must be callable.")
  if not callable(fn2):
    raise TypeError("fn2 must be callable.")
  if pred is True:
    return fn1()
  if pred is False:
    return fn2()
  from tensorflow.python.framework import tensor_util
  pred_const = tensor_util.constant_value(pred)
  if pred_const is not None:
    if pred_const:
      return fn1()
    else:
      return fn2()
  from tensorflow.python.ops import control_flow_ops
  return control_flow_ops.cond(pred, fn1, fn2, name=name)


def single_strided_slice(x, axis, begin=None, end=None, step=None):
  """
  :param tf.Tensor x:
  :param int|tf.Tensor axis:
  :param int|tf.Tensor|None begin:
  :param int|tf.Tensor|None end:
  :param int|tf.Tensor|None step:
  :return: e.g. if axis == 0, returns x[begin:end:step], if axis == 1, returns x[:, begin:end:step], etc.
  :rtype: tf.Tensor
  """
  with tf.name_scope("single_strided_slice"):
    if isinstance(axis, int):
      if axis < 0 and x.get_shape().ndims is not None:
        axis %= x.get_shape().ndims
        assert axis >= 0
      if axis >= 0:
        return x[(slice(None),) * axis + (slice(begin, end, step),)]
    else:
      assert isinstance(axis, tf.Tensor)
    axis = axis % tf.rank(x)
    shape = tf.shape(x)
    if begin is None:
      begin = 0
    if end is None:
      end = shape[axis]
    begins = tf.concat([tf.zeros((axis,), tf.int32), (begin,)], axis=0)
    ends = tf.concat([shape[:axis], (end,)], axis=0)
    if step is not None:
      strides = tf.concat([tf.ones((axis,), tf.int32), (step,)], axis=0)
    else:
      strides = None
    return tf.strided_slice(x, begin=begins, end=ends, strides=strides)


def circular_pad(x, paddings, axes=None):
  """
  :param tf.Tensor x: shape (..., height, width)
  :param int|((int,int), (int,int))|tf.Tensor paddings: how much to add ((top,bottom),(left,right))
  :return: tensor with shape (..., top + height + bottom, left + width + right)
  :rtype: tf.Tensor
  """
  with tf.name_scope("circular_pad"):
    ndim = x.get_shape().ndims
    assert ndim is not None
    shape = tf.shape(x)
    if axes is None:
      axis_height = ndim - 2
      axis_width = ndim - 1
    elif isinstance(axes, tf.Tensor):
      axes = check_input_ndim(axes, 1)
      axes = check_input_dim(axes, 0, 2)
      axis_height, axis_width = axes[0], axes[1]
    else:
      axis_height, axis_width = axes
    height, width = shape[axis_height], shape[axis_width]
    if isinstance(paddings, tf.Tensor):
      paddings = check_input_ndim(paddings, 2)
      paddings = check_input_dim(paddings, 0, 2)
      paddings = check_input_dim(paddings, 1, 2)
      top, bottom = paddings[0, 0], paddings[0, 1]
      left, right = paddings[1, 0], paddings[1, 1]
    elif isinstance(paddings, int):
      top = bottom = left = right = paddings
    else:
      assert isinstance(paddings, (list, tuple))
      (top, bottom), (left, right) = paddings
    left_x = single_strided_slice(x, begin=width - left, axis=axis_width)
    right_x = single_strided_slice(x, end=right, axis=axis_width)
    left_right_and_x = tf.concat([left_x, x, right_x], axis=axis_width)  # shape (..., height, left + width + right)
    top_x = single_strided_slice(left_right_and_x, begin=height - top, axis=axis_height)
    bottom_x = single_strided_slice(left_right_and_x, end=bottom, axis=axis_height)
    all_combined_x = tf.concat([top_x, left_right_and_x, bottom_x], axis=axis_height)  # final shape
    assert isinstance(all_combined_x, tf.Tensor)
    return all_combined_x


def spatial_smoothing_energy(x, dim, use_circular_conv=True):
  """
  :param tf.Tensor x: shape (..., dim)
  :param int dim: last dimension of x
  :param bool use_circular_conv: whether to use circular convolution, via circular_pad
  :rtype: tf.Tensor
  :return: energy of shape (...)

  Via Achieving Human Parity in Conversational Speech Recognition, Microsoft, 2017.
  Interpret the last dimension as 2D (w, h) and apply some high-pass filter on it.
  """
  import math
  with tf.name_scope("spatial_smoothing_energy"):
    x = check_input_dim(x, -1, dim)
    shape = get_shape(x)
    w = int(math.sqrt(dim))
    while dim % w > 0:
      w -= 1
      assert w > 0
    h = dim // w
    assert w * h == dim
    assert w >= 3 and h >= 3, "too small"
    # input shape: [batch, in_height=h, in_width=w, in_channels=1]
    x = tf.reshape(x, [-1, h, w, 1])
    if use_circular_conv:
      x = circular_pad(x, paddings=1, axes=(1, 2))  # [batch, h+2, w+2, in_channels=1]
    # filter shape: [filter_height, filter_width, in_channels=1, out_channels=1]
    filter = tf.reshape(tf.constant(
      [[-0.125, -0.125, -0.125],
       [-0.125, 1.0, -0.125],
       [-0.125, -0.125, -0.125]]), [3, 3, 1, 1])
    # out shape: [batch, out_height, out_width, out_channels=1]
    out = tf.nn.conv2d(x, filter=filter, strides=[1, 1, 1, 1], padding="VALID")
    out = tf.reshape(out, shape[:-1] + [-1])  # (..., out_height*out_width)
    # Note: Square all the filter values.
    return tf.reduce_sum(out ** 2, axis=-1)


def nan_to_num(x, nan_num=0, inf_num=1e30):
  """
  Like numpy.nan_to_num().

  :param tf.Tensor x:
  :param float|tf.Tensor nan_num:
  :param float|tf.Tensor inf_num:
  :return: x with replaced nan and inf
  """
  with tf.name_scope("nan_to_num"):
    nan_num = tf.convert_to_tensor(nan_num, dtype=x.dtype)
    inf_num = tf.convert_to_tensor(inf_num, dtype=x.dtype)
    # Note that tf.where() does not support broadcasting at the moment,
    # so we need the same shape. The following will do that.
    # This should be removed once tf.where() supports broadcasting.
    # https://github.com/tensorflow/tensorflow/issues/3945
    nan_num = tf.ones_like(x) * nan_num
    inf_num = tf.ones_like(x) * inf_num
    x = tf.where(tf.is_nan(x), nan_num, x)
    x = tf.where(tf.logical_and(tf.is_inf(x), tf.greater(x, 0)), inf_num, x)
    x = tf.where(tf.logical_and(tf.is_inf(x), tf.less(x, 0)), -inf_num, x)
    return x


def identity_op_nested(x, name="identity"):
  """
  :param tf.Tensor|list[tf.Tensor]|dict[str,tf.Tensor] x:
  :param str name:
  :rtype tf.Tensor|list[tf.Tensor]|dict[str,tf.Tensor]
  """
  if isinstance(x, dict):
    return {k: identity_op_nested(x[k], name="%s_%s" % (name, k)) for k in x}
  if isinstance(x, (list, tuple)):
    return [identity_op_nested(x[i], name="%s_%i" % (name, i)) for i in range(len(x))]
  assert isinstance(x, tf.Tensor)
  return tf.identity(x, name=name)


def nd_indices(indices, batch_axis=0):
  """
  :param tf.Tensor indices: e.g. (batch, ...) -> index
  :return: extended indices with batch-idx which can be used for tf.gather_nd,
    i.e. in the example of shape (batch, ..., 2) where the 2-tuple represents (batch_idx, index).
  :rtype: tf.Tensor
  """
  assert indices.get_shape().ndims >= 1
  assert batch_axis < indices.get_shape().ndims
  with tf.name_scope("nd_indices"):
    batches_idxs = tf.range(tf.shape(indices)[batch_axis], name="batches_idxs")  # (batch,)
    batches_idxs = tf.cast(batches_idxs, dtype=indices.dtype)
    for axis in range(indices.get_shape().ndims):
      if axis == batch_axis:
        continue
      batches_idxs = expand_dims_unbroadcast(batches_idxs, axis=axis, dim=tf.shape(indices)[axis],
                                             name="batches_idxs_bc")  # (batch, ...)
    batches_idxs.set_shape(indices.get_shape())
    idxs_exp = tf.stack([batches_idxs, indices], axis=-1,
                        name="idxs_exp")  # (batch,...,2), where the 2 stands for (batch_idx, index)
    return idxs_exp


def stop_event_writer_thread(event_writer):
  """
  There is a bug in TensorFlow (at least 1.1.0) (https://github.com/tensorflow/tensorflow/issues/4820)
  that the event writer thread is never stopped.
  This will try to stop it. Only do it if you don't use the event writer anymore.

  :param tensorflow.python.summary.writer.event_file_writer.EventFileWriter event_writer:
  """
  from tensorflow.python.summary.writer.event_file_writer import EventFileWriter, _EventLoggerThread
  assert isinstance(event_writer, EventFileWriter)
  if not event_writer._worker:  # maybe fixed already?
    return

  # This solution is very ugly and dependent on TF internal code.
  class DummyStopThread:
    @classmethod
    def WriteEvent(cls, *args, **kwargs):
      raise SystemExit  # stop the thread

  assert isinstance(event_writer._worker, _EventLoggerThread)
  worker = event_writer._worker
  worker._ev_writer = DummyStopThread
  worker._queue.put(None)
  worker.join()


def optional_add(*args):
  """
  :param list[tf.Tensor|None]|tf.Tensor args:
  :rtype: tf.Tensor|None
  :return: sums all non-None values, or returns None if there are none
  """
  y = None
  for v in args:
    if v is not None:
      if y is None:
        y = v
      else:
        y = y + v
  return y


def windowed_nd(source, window, padding="same", time_axis=1, new_window_axis=2):
  """
  :param tf.Tensor source: N-D tensor of shape (..., n_time, ...)
  :param int|tf.Tensor window: window size
  :param str padding: "same" or "valid"
  :param int time_axis:
  :param int new_window_axis:
  :return: tensor of shape (..., n_time, ..., window, ...)
  :rtype: tf.Tensor
  """
  with tf.name_scope("windowed_batch"):
    if time_axis != 0:
      source = move_axis(source, time_axis, 0)  # (n_time,...)
    source_shape = tf.shape(source)
    n_time = source_shape[0]
    if padding == "same":
      n_out_time = n_time
      w_right = window // 2
      w_left = window - w_right - 1
      pad_left = tf.zeros(tf.concat([[w_left], source_shape[1:]], axis=0), dtype=source.dtype)
      pad_right = tf.zeros(tf.concat([[w_left], source_shape[1:]], axis=0), dtype=source.dtype)
      source = tf.concat([pad_left, source, pad_right], axis=0)  # shape[0] == n_time + window - 1
    elif padding == "valid":
      n_out_time = n_time - window + 1
    else:
      raise Exception("invalid padding %r" % padding)
    tiled_dimshuffle = expand_dims_unbroadcast(source, axis=0, dim=window)  # (window,n_time+window-1,...)
    # We want to shift every dim*time block by one to the left.
    # To do this, we interpret that we have one more time frame (i.e. n_time+window).
    # We have to do some dimshuffling so that we get the right layout, then we can flatten,
    # add some padding, and then dimshuffle it back.
    # Then we can take out the first n_time frames.
    tiled_flat = tf.reshape(tiled_dimshuffle, [-1])
    rem = window * tf.reduce_prod(source_shape[1:])
    tiled_flat_pad_right = tf.concat([tiled_flat, tf.zeros((rem,), dtype=source.dtype)], axis=0)
    tiled_reshape_shift = tf.reshape(
      tiled_flat_pad_right,
      tf.concat([(window, n_time + window),
                 source_shape[1:]], axis=0))  # add time frame, (window,n_time+window,...)
    final = tiled_reshape_shift
    if new_window_axis != 0:
      final = move_axis(final, 0, new_window_axis)  # (n_time+window,...,window,...)
      final = final[:n_out_time]  # (n_out_time,...,window,...)
    else:
      final = final[:, :n_out_time]  # (window,n_out_time,...)
    # Move time-axis back to its original place.
    if new_window_axis <= time_axis:
      time_axis += 1  # New window axis was inserted before.
    if time_axis != 0:
      if new_window_axis != 0:
        final = move_axis(final, 0, time_axis)
      else:
        final = move_axis(final, 1, time_axis)
    return final


def global_tensor(f, name):
  """
  This creates a global accessible tensor in the graph to be reused later,
  i.e. on the second call given a unique name, it will not create a new tensor
  but return the previously created tensor.
  This is for the current graph, i.e. if there is a new graph, it will recreate the tensor.

  :param () -> tf.Tensor f: callable which creates the tensor
  :param str name: global reference name for the tensor
  :return: the tensor
  :rtype: tf.Tensor
  """
  graph = tf.get_default_graph()
  assert isinstance(graph, tf.Graph)
  abs_graph_name = "globals/%s:0" % name
  try:
    return graph.get_tensor_by_name(abs_graph_name)
  except KeyError:  # does not exist yet
    pass
  with tf.name_scope("global_tensor_%s" % name):  # relative to the current scope
    v = f()
  with tf.name_scope("globals/"):  # enter the absolute scope
    v = tf.identity(v, name=name)
  assert isinstance(v, tf.Tensor)
  assert v.name == abs_graph_name
  assert graph.get_tensor_by_name(abs_graph_name) is v
  return v


def get_global_train_flag_placeholder():
  """
  :return: bool scalar tensor
  :rtype: tf.Tensor
  """
  return global_tensor(
    lambda: tf.placeholder(tf.bool, shape=(), name="train_flag"),
    name="train_flag")


def encode_raw(x, axis=-1, seq_lens=None):
  """
  The inverse function of tf.decode_raw().
  Also see: https://stackoverflow.com/questions/43403147/how-to-create-a-encode-raw-tensorflow-function

  :param tf.Tensor x: of integer types [0,255], will get casted to uint8
  :param int axis: the axis to reduce-join the string. decode_raw has added it at the end
  :param tf.Tensor|None seq_lens: must have same shape as x after reduce-joining.
    Note that using seq_lens will make our output not compatible with tf.decode_raw() anymore
    because tf.decode_raw() requires all strings to be of the same length.
  :return: string tensor
  :rtype: tf.Tensor
  """
  with tf.name_scope("encode_raw"):
    character_lookup = global_tensor(
      lambda: tf.constant([chr(i) for i in range(256)]), name="character_lookup")
    raw_bytes = tf.bitcast(x, tf.uint8, name="raw_bytes")
    chars = tf.gather(character_lookup, indices=tf.cast(raw_bytes, tf.int32), name="chars")
    strings = tf.reduce_join(chars, axis=axis, name="strings")
    if seq_lens is not None:
      strings = tf.substr(strings, pos=tf.zeros_like(seq_lens), len=seq_lens)
    return strings


def pad_zeros_in_axis(x, before=0, after=0, axis=0):
  """
  :param tf.Tensor x:
  :param int|tf.Tensor before:
  :param int|tf.Tensor after:
  :param int axis:
  :return:
  """
  with tf.name_scope("pad_zeros_in_axis"):
    paddings = [[0, 0] for i in range(x.get_shape().ndims)]
    paddings[axis] = [before, after]
    return tf.pad(x, paddings=paddings)


def slice_pad_zeros(x, begin, end, axis=0):
  """
  :param tf.Tensor x: of shape (..., time, ...)
  :param int|tf.Tensor begin:
  :param int|tf.Tensor end:
  :param int axis:
  :return: basically x[begin:end] (with axis==0) but if begin < 0 or end > x.shape[0],
   it will not discard these frames but pad zeros, such that the resulting shape[0] == end - begin.
  :rtype: tf.Tensor
  """
  with tf.name_scope("slice_pad_zeros"):
    min_frame = tf.minimum(begin, end)
    left_rem = -min_frame
    x, begin, end = tf.cond(
      tf.less_equal(left_rem, 0),
      lambda: [x, begin, end],
      lambda: [pad_zeros_in_axis(x, before=left_rem, axis=axis), begin + left_rem, end + left_rem])
    max_frame = tf.maximum(begin, end)
    right_rem = max_frame - tf.shape(x)[axis]
    x = tf.cond(
      tf.less_equal(right_rem, 0),
      lambda: x,
      lambda: pad_zeros_in_axis(x, after=right_rem, axis=axis))
    return single_strided_slice(x, axis=axis, begin=begin, end=end)


def post_control_dependencies(x, updates):
  """
  :param tf.Tensor|list[tf.Tensor]|dict[str,tf.Tensor] x:
  :param list[tf.Operation] updates:
  :return: identity(x) with control_dependencies(updates)
  :rtype: tf.Tensor|list[tf.Tensor]|dict[str,tf.Tensor]
  """
  with tf.name_scope("post_control_dependencies"):
    with tf.control_dependencies(updates):
      if isinstance(x, tf.Tensor):
        return tf.identity(x)
      elif isinstance(x, (tuple, list)):
        return [tf.identity(v) for v in x]
      elif isinstance(x, dict):
        return {k: tf.identity(v) for (k, v) in x.items()}
      else:
        raise ValueError("type of %r not expected" % x)


@contextlib.contextmanager
def sequential_control_dependencies(l):
  """
  tf.control_dependencies but each operation will be created such that it is executed
  after the ones coming before in the list, i.e. l[0] is executed first, l[-1] is executed last.

  :param list[()->(tf.Operation|tf.Tensor)] l:
  """
  with tf.control_dependencies([l[0]()]) as dep:
    if len(l) > 1:
      with sequential_control_dependencies(l[1:]) as dep2:
        yield dep2
    else:
      yield dep


def global_queue(name, queue_type, capacity, dtypes, shapes=None, names=None):
  """
  :param (args)->tf.QueueBase queue_type: some function which creates a queue
  :param str name: global name
  :param list[tf.DType|str] dtypes:
  :param list[tf.TensorShape|tuple[int|None]]|None shapes:
  :param list[str]|None names:
  :rtype: tf.QueueBase
  """
  queue_ref = global_tensor(
    name=name,
    f=lambda: queue_type(capacity=capacity, dtypes=dtypes, shapes=shapes, names=names).queue_ref)
  queue = tf.QueueBase(dtypes=dtypes, shapes=shapes, names=names, queue_ref=queue_ref)
  return queue


def init_variable_if_needed(v):
  """
  :param tf.Variable v:
  :rtype: tf.Operation
  """
  def make_init():
    # Cannot use tf.variables_initializer(), see here: https://stackoverflow.com/questions/44354964/
    with tf.control_dependencies([tf.assign(v, v.initial_value)]):
      return tf.no_op()

  maybe_init = tf.cond(
    tf.is_variable_initialized(v),
    lambda: tf.no_op(),
    make_init,
    name="maybe_init")

  return maybe_init


def auto_init_var(v):
  """
  :param tf.Variable v:
  :return: a reference to the var via tf.identity
  :rtype: tf.Tensor
  """
  with tf.control_dependencies(init_variable_if_needed(v)):
    return tf.identity(v)


def true_once():
  """
  :return: tensor which will be True once and then always False
    Internally, this creates a non-trainable variable as a helper.
  :rtype: tf.Tensor
  """
  v = tf.Variable(initial_value=True, trainable=False, name="true_once_var")
  with tf.control_dependencies([init_variable_if_needed(v)]):
    # Cannot use tf.identity because that would give us a reference to the var but we want to copy it now.
    x = tf.where(v.read_value(), True, False)
    with tf.control_dependencies([x]):
      x = tf.identity(x)
      reset = tf.assign(v, False)
      with tf.control_dependencies([x, reset]):
        x = tf.identity(x)
  return x


def raise_OutOfRangeError():
  """
  :return: an op which raises an OutOfRangeError
  :rtype: tf.Operation
  """
  # Kind of hacky. We create some dummy queue, close it and every time we call dequeue on it,
  # it will raise the desired exception.
  with tf.name_scope("raise_OutOfRangeError"):
    queue = global_queue(name="raise_exception/queue", queue_type=tf.FIFOQueue, capacity=1, dtypes=[tf.bool])
    # We must only close it once, otherwise we could get a CancelledError.
    queue_open = global_tensor(f=true_once, name="raise_exception/queue_open")
    with tf.control_dependencies([tf.cond(queue_open, lambda: queue.close(), lambda: tf.no_op())]):
      return queue.dequeue()


def enforce_copy(x):
  """
  :param tf.Tensor|tf.Variable x:
  :return: copy of input, i.e. enforces that this is not a ref
  """
  with tf.name_scope("copy"):
    zero = x.dtype.as_numpy_dtype()
    return tf.add(x, zero)


class Lock(object):
  """
  A pure TensorFlow implementation of a mutex / lock.
  """
  def __init__(self, name="Lock"):
    self._name = name
    with tf.name_scope(self._name):
      from tensorflow.python.ops.data_flow_ops import StagingArea
      self._queue = StagingArea(dtypes=[tf.bool])
      self._queue_init = self._queue.put([True])

  def init(self):
    return self._queue_init

  def lock(self):
    """
    On first call, just returns. Any further call will block, unless there is an unlock() call.
    """
    with tf.name_scope("%s/lock" % self._name):
      return self._queue.get()

  def unlock(self):
    """
    Must be called after lock().
    """
    with tf.name_scope("%s/unlock" % self._name):
      return self._queue.put([True])


class Condition(object):
  """
  A pure TensorFlow implementation of a condition.
  """
  def __init__(self, lock=None, name="Condition"):
    self._name = name
    with tf.variable_scope(name):
      self._init_ops = []
      if not lock:
        lock = Lock()
        self._init_ops += [lock.init()]
      self.lock = lock
      self._waiting_counter = tf.Variable(initial_value=0, trainable=False, name="waiting_counter")
      self._waiter_queue = tf.FIFOQueue(capacity=1, dtypes=[tf.bool], name="waiter_queue")
      self._init_ops += [self._waiting_counter.initializer]

  def init(self):
    return tf.group(*self._init_ops)

  def wait(self):
    """
    Must be called with the lock held, will unlock while waiting for a signal.
    """
    with tf.name_scope("%s/wait" % self._name):
      with sequential_control_dependencies([
        lambda: self._waiting_counter.assign_add(1, use_locking=True),
        lambda: self.lock.unlock(),
        lambda: self._waiter_queue.dequeue(),
        lambda: self.lock.lock(),
        lambda: self._waiting_counter.assign_sub(1, use_locking=True)
      ]):
        return tf.no_op()

  def wait_counter(self):
    return enforce_copy(self._waiting_counter.read_value())

  def signal(self):
    """
    Must be called with the lock held.
    Emits one signal.
    """
    with tf.name_scope("%s/signal" % self._name):
      def on_waiting_counter():
        return self._waiter_queue.enqueue(True)
      return tf.cond(tf.greater(self._waiting_counter.read_value(), 0), on_waiting_counter, lambda: tf.no_op())

  def signal_all(self):
    """
    Must be called with the lock held.
    Emits as many signals as they are waiters.
    """
    with tf.name_scope("%s/signal_all" % self._name):
      count = self.wait_counter()
      with sequential_control_dependencies([lambda: count, lambda: self.lock.unlock()]):
        # We must unlock because we could have to do multiple signals but the waiter-queue has only capacity 1,
        # i.e. we would (dead)lock otherwise.
        def body(i):
          with tf.control_dependencies([i]):
            with tf.control_dependencies([self._waiter_queue.enqueue(False)]):
              return i + 1
        loop = tf.while_loop(
          cond=lambda i: tf.less(i, count),
          body=body, parallel_iterations=1, back_prop=False, loop_vars=[0])
        with tf.control_dependencies([loop]):
          return self.lock.lock()


class GlobalTensorArrayOpMaker:
  """
  Creates a TensorArray which does not use the per-run ("per-step") resource manager container
  but uses the standard container which persists across runs.
  This TensorArray resource handle is then just a standard TensorArray resource handle which
  can be used with all TensorArray related functions/ops.

  Note: This whole implementation currently does not work because tensor_array.h is not available.
  See https://github.com/tensorflow/tensorflow/issues/10527
  and test_GlobalTensorArray().
  """

  code = """
    #include "tensorflow/core/framework/op_kernel.h"
    #include "tensorflow/core/framework/register_types.h"
    #include "tensorflow/core/framework/resource_mgr.h"
    #include "tensorflow/core/framework/tensor.h"
    #include "tensorflow/core/framework/tensor_shape.h"
    #include "tensorflow/core/framework/types.h"
    #include "tensorflow/core/kernels/bounds_check.h"
    #include "tensorflow/core/kernels/tensor_array.h"
    #include "tensorflow/core/lib/core/errors.h"
    #include "tensorflow/core/lib/core/refcount.h"
    #include "tensorflow/core/lib/strings/strcat.h"
    #include "tensorflow/core/platform/dynamic_annotations.h"
    #include "tensorflow/core/platform/logging.h"
    #include "tensorflow/core/platform/thread_annotations.h"
    #include "tensorflow/core/platform/types.h"

    using namespace tensorflow;
  
    // Adopted from https://github.com/tensorflow/tensorflow/blob/master/tensorflow/core/ops/data_flow_ops.cc.
    REGISTER_OP("GlobalTensorArray")
    .Input("size: int32")
    .Attr("container: string = ''")
    .Attr("shared_name: string = ''")
    .Attr("dtype: type")
    .Attr("element_shape: shape = { unknown_rank: true }")
    .Attr("dynamic_size: bool = false")
    .Attr("clear_after_read: bool = true")
    .Attr("tensor_array_name: string = ''")
    .Output("handle: resource")
    .Output("flow: float")
    .SetIsStateful()
    .SetShapeFn([](InferenceContext* c) {
      ShapeHandle unused;
      TF_RETURN_IF_ERROR(c->WithRank(c->input(0), 0, &unused));
      c->set_output(0, c->Vector(2));
      c->set_output(1, c->Scalar());
      return Status::OK();
    })
    .Doc("GlobalTensorArray, persistent across runs");
    
    // Copied from https://github.com/tensorflow/tensorflow/blob/master/tensorflow/core/kernels/tensor_array_ops.cc,
    // and https://github.com/tensorflow/tensorflow/blob/master/tensorflow/core/framework/resource_op_kernel.h.
    // The original TensorArrayOp used the per-run ("per-step") resource manager container
    // but we use the standard container which persists across runs.
    class GlobalTensorArrayOp : public OpKernel {
     public:
      explicit GlobalTensorArrayOp(OpKernelConstruction* context)
          : OpKernel(context), device_type_(context->device_type()) {
        OP_REQUIRES_OK(context, context->GetAttr("dtype", &dtype_));
        OP_REQUIRES_OK(context, context->GetAttr("element_shape", &element_shape_));
        OP_REQUIRES_OK(context, context->GetAttr("dynamic_size", &dynamic_size_));
        OP_REQUIRES_OK(context,
                       context->GetAttr("clear_after_read", &clear_after_read_));
        OP_REQUIRES_OK(context,
                       context->GetAttr("tensor_array_name", &tensor_array_name_));
        if (tensor_array_name_.empty()) tensor_array_name_ = name();

        AllocatorAttributes alloc_attr;
        alloc_attr.set_on_host(true);
        OP_REQUIRES_OK(context, context->allocate_persistent(
                                tensorflow::DT_STRING, tensorflow::TensorShape({2}),
                                &handle_, alloc_attr));
      }
    
      ~GlobalTensorArrayOp() {
        if (resource_ != nullptr) {
          resource_->Unref();
          if (cinfo_.resource_is_private_to_kernel()) {
            if (!cinfo_.resource_manager()
                     ->template Delete<T>(cinfo_.container(), cinfo_.name())
                     .ok()) {
              // Do nothing; the resource can have been deleted by session resets.
            }
          }
        }
      }
    
      void Compute(OpKernelContext* ctx) override {
        mutex_lock l(mu_);
        if (resource_ == nullptr) {
          ResourceMgr* mgr = ctx->resource_manager();
          OP_REQUIRES(ctx, mgr != nullptr, errors::Internal("No resource manager."));
          OP_REQUIRES_OK(ctx, cinfo_.Init(mgr, def()));
          auto h = handle_.AccessTensor(ctx)->template flat<string>();
          h(0) = cinfo_.container();
          h(1) = cinfo_.name();
          OP_REQUIRES_OK(ctx, CreateTensorArray(ctx, rm, &handle_, &resource_));
        }

        Tensor* handle;
        OP_REQUIRES_OK(ctx, ctx->allocate_output(0, TensorShape({}), &handle));
        handle->flat<ResourceHandle>()(0) =
            resource_->resource_handle(ctx);            
        if (ctx->num_outputs() == 2) {
          // Create the flow output.
          Tensor* flow;
          OP_REQUIRES_OK(ctx, ctx->allocate_output(1, TensorShape({}), &flow));
          if (device_type_ == DEVICE_CPU) {
            // Value doesn't matter, but this makes msan not complaint about
            // copying an uninitialized value. To do this on GPU would require
            // a kernel launch or a host->device memcpy, so we avoid that.
            flow->flat<float>()(0) = 0;
          }
        }
      }
    
     private:
      Status CreateTensorArray(OpKernelContext* ctx, ResourceMgr* rm,
                               Tensor* tensor_array_output_handle,
                               TensorArray** output_tensor_array) EXCLUSIVE_LOCKS_REQUIRED(mu_) {
        const Tensor* tensor_size;
        TF_RETURN_IF_ERROR(ctx->input("size", &tensor_size));
    
        if (!TensorShapeUtils::IsScalar(tensor_size->shape())) {
          return errors::InvalidArgument(
              "TensorArray size must be scalar, but had shape: ",
              tensor_size->shape().DebugString());
        }
        const int32 size = tensor_size->scalar<int32>()();
        if (size < 0) {
          return errors::InvalidArgument("Size should be >= 0.");
        }
    
        TensorArray* tensor_array = new TensorArray(
            cinfo_.name(), dtype_, *tensor_array_output_handle, size, element_shape_,
            dynamic_size_, false /* multiple_writes_aggregate */,
            false /* is_grad */, -1 /* marked_size */, clear_after_read_);
    
        // TODO: could use LookupOrCreate instead...
        TF_RETURN_IF_ERROR(
            rm->Create(cinfo_.container(), cinfo_.name(), tensor_array));
    
        *output_tensor_array = tensor_array;
    
        return Status::OK();
      }

      mutex mu_;
      ContainerInfo cinfo_ GUARDED_BY(mu_);
      PersistentTensor handle_ GUARDED_BY(mu_);
      TensorArray* resource_ GUARDED_BY(mu_) = nullptr;
      
      const DeviceType device_type_;
      DataType dtype_;
      PartialTensorShape element_shape_;
      bool dynamic_size_;
      bool clear_after_read_;
      string tensor_array_name_;  // The name used to create the TensorArray.
      
      TF_DISALLOW_COPY_AND_ASSIGN(GlobalTensorArrayOp);
    };
    
    REGISTER_KERNEL_BUILDER(Name("GlobalTensorArray").Device(DEVICE_CPU), GlobalTensorArrayOp);

  """

  def __init__(self):
    self._mod = None

  def _make_mod(self):
    if self._mod:
      return self._mod

    comp = OpCodeCompiler(
      base_name="GlobalTensorArray",
      code_version=1,  # code also ends up in hash, thus this doesn't always needs to be increased
      code=self.code,
      include_deps=[],
      ld_flags=[])

    mod = comp.load_module()
    self._mod = mod
    return mod

  def get_op(self):
    mod = self._make_mod()
    from Util import camel_case_to_snake_case
    op = getattr(mod, camel_case_to_snake_case("GlobalTensorArray"))
    return op


class TFArrayContainer(object):
  """
  Array container, like std::vector, with random index access.

  Currently does not work.
  See https://github.com/tensorflow/tensorflow/issues/10950,
  and test_TFArrayContainer().
  """

  code = """
    #include <vector>

    // For Eigen::ThreadPoolDevice.
    #define EIGEN_USE_THREADS 1

    #include "tensorflow/core/framework/op.h"
    #include "tensorflow/core/framework/shape_inference.h"
    #include "tensorflow/core/framework/op_kernel.h"
    #include "tensorflow/core/framework/resource_mgr.h"
    #include "tensorflow/core/framework/resource_op_kernel.h"
    #include "tensorflow/core/framework/tensor.h"
    #include "tensorflow/core/framework/tensor_shape.h"
    #include "tensorflow/core/framework/types.h"
    #include "tensorflow/core/platform/macros.h"
    #include "tensorflow/core/platform/mutex.h"
    #include "tensorflow/core/platform/types.h"

    using namespace tensorflow;

    REGISTER_OP("ArrayContainerCreate")
    .Attr("T: type")
    .Attr("container: string = ''")
    .Attr("shared_name: string = ''")
    .Output("resource: resource")
    .SetIsStateful()
    .SetShapeFn(shape_inference::ScalarShape)
    .Doc(R"doc(Array container, random index access)doc");

    REGISTER_OP("ArrayContainerGetSize")
    .Input("handle: resource")
    .Output("out: int32")
    .SetShapeFn(shape_inference::ScalarShape)
    ;

    REGISTER_OP("ArrayContainerSetSize")
    .Attr("T: type")
    .Input("handle: resource")
    .Input("size: int32")
    ;

    REGISTER_OP("ArrayContainerGet")
    .Attr("T: type")
    .Input("handle: resource")
    .Input("index: int32")
    .Output("out: T")
    ;

    REGISTER_OP("ArrayContainerSet")
    .Attr("T: type")
    .Input("handle: resource")
    .Input("index: int32")
    .Input("value: T")
    ;

    // https://github.com/tensorflow/tensorflow/blob/master/tensorflow/core/framework/resource_mgr.h
    struct ArrayContainer : public ResourceBase {
      ArrayContainer(const DataType& dtype) : dtype_(dtype) {}

      string DebugString() override { return "ArrayContainer"; }
      int64 MemoryUsed() const override { return 0; };

      mutex mu_;
      const DataType dtype_;
      std::vector<PersistentTensor> data_ GUARDED_BY(mu_);

      int32 get_size() {
        mutex_lock l(mu_);
        return (int32) data_.size();
      }

      Status set_size(int32 size) {
        if(size < 0)
          return errors::InvalidArgument("size ", size, " must be >= 0");
        mutex_lock l(mu_);
        data_.resize((size_t) size);
        return Status::OK();
      }

      Status get(OpKernelContext* ctx, int32 idx, PersistentTensor* v) {
        mutex_lock l(mu_);
        if(idx < 0)
          return errors::InvalidArgument("idx ", idx, " must be >= 0");
        if((size_t)idx >= data_.size())
          return errors::InvalidArgument("idx ", idx, " must be < size ", data_.size());
        PersistentTensor& t = data_[(size_t)idx];
        if(!t.IsInitialized())
          return errors::InvalidArgument("tensor at idx ", idx, " must have been set before");
        *v = t;
        return Status::OK();
      }

      Status set(OpKernelContext* ctx, int32 idx, const Tensor& v) {
        mutex_lock l(mu_);
        if(idx < 0)
          return errors::InvalidArgument("idx ", idx, " must be >= 0");
        if((size_t)idx >= data_.size())
          return errors::InvalidArgument("idx ", idx, " must be < size ", data_.size());
        data_[idx] = PersistentTensor(v);
        return Status::OK();
      }

    };

    ResourceHandle OwnMakeResourceHandle(OpKernelContext* ctx, const string& container,
                                         const string& name,
                                         const TypeIndex& type_index) {
      ResourceHandle result;
      result.set_device(ctx->device()->attributes().name());
      printf("make dev %s\\n", result.device().c_str());
      string actual_container;
      if (!container.empty()) {
        actual_container = container;
      } else {
        actual_container = ctx->resource_manager()->default_container();
      }
      result.set_container(actual_container);
      result.set_name(name);
      result.set_hash_code(type_index.hash_code());
      result.set_maybe_type_name(type_index.name());
      printf("make dev %s\\n", result.device().c_str());
      return result;
    }

    // https://github.com/tensorflow/tensorflow/blob/master/tensorflow/core/framework/resource_op_kernel.h
    class ArrayContainerCreateOp : public ResourceOpKernel<ArrayContainer> {
    public:
      explicit ArrayContainerCreateOp(OpKernelConstruction* context) : ResourceOpKernel(context) {
        OP_REQUIRES_OK(context, context->GetAttr("T", &dtype_));
      }

      void Compute(OpKernelContext* context) override {
        ResourceOpKernel<ArrayContainer>::Compute(context);
        mutex_lock l(mu_);
        ResourceHandle rhandle = OwnMakeResourceHandle(context, cinfo_.container(), cinfo_.name(), MakeTypeIndex<ArrayContainer>());
        printf("created. device: %s\\n", rhandle.device().c_str());
        printf("container: %s\\n", rhandle.container().c_str());
        printf("name: %s\\n", rhandle.name().c_str());
        printf("actual device: %s\\n", context->device()->attributes().name().c_str());
        printf("actual name: %s\\n", cinfo_.name().c_str());
        rhandle.set_device("foo");
        printf("now device: %s\\n", rhandle.device().c_str());
        ResourceHandle cpy = rhandle;
        printf("cpy device: %s\\n", cpy.device().c_str());
      }
      
    private:
      virtual bool IsCancellable() const { return false; }
      virtual void Cancel() {}

      Status CreateResource(ArrayContainer** ret) override EXCLUSIVE_LOCKS_REQUIRED(mu_) {
        *ret = new ArrayContainer(dtype_);
        if(*ret == nullptr)
          return errors::ResourceExhausted("Failed to allocate");
        return Status::OK();
      }

      Status VerifyResource(ArrayContainer* ar) override {
        if(ar->dtype_ != dtype_)
          return errors::InvalidArgument("Data type mismatch: expected ", DataTypeString(dtype_),
                                         " but got ", DataTypeString(ar->dtype_), ".");
        return Status::OK();
      }
  
      DataType dtype_;
    };
    REGISTER_KERNEL_BUILDER(Name("ArrayContainerCreate").Device(DEVICE_CPU), ArrayContainerCreateOp);

    class ArrayContainerGetSizeOp : public OpKernel {
    public:
      using OpKernel::OpKernel;

      void Compute(OpKernelContext* context) override {
        ArrayContainer* ar;
        
        const Tensor* handle;
        OP_REQUIRES_OK(context, context->input("handle", &handle));
        const ResourceHandle& rhandle = handle->scalar<ResourceHandle>()();
        printf("device: %s\\n", rhandle.device().c_str());
        printf("container: %s\\n", rhandle.container().c_str());
        printf("name: %s\\n", rhandle.name().c_str());
        
        OP_REQUIRES_OK(context, GetResourceFromContext(context, "handle", &ar));
        core::ScopedUnref unref(ar);

        int32 size = ar->get_size();
        Tensor* tensor_size = nullptr;
        OP_REQUIRES_OK(context, context->allocate_output(0, TensorShape({}), &tensor_size));
        tensor_size->flat<int32>().setConstant(size);
      }
    };
    REGISTER_KERNEL_BUILDER(Name("ArrayContainerGetSize").Device(DEVICE_CPU), ArrayContainerGetSizeOp);

    class ArrayContainerSetSizeOp : public OpKernel {
    public:
      using OpKernel::OpKernel;

      void Compute(OpKernelContext* context) override {
        ArrayContainer* ar;
        OP_REQUIRES_OK(context, GetResourceFromContext(context, "handle", &ar));
        core::ScopedUnref unref(ar);

        const Tensor* tensor_size;
        OP_REQUIRES_OK(context, context->input("size", &tensor_size));
        OP_REQUIRES(context, TensorShapeUtils::IsScalar(tensor_size->shape()),
                    errors::InvalidArgument(
                        "TensorArray index must be scalar, but had shape: ",
                        tensor_size->shape().DebugString()));
        const int32 size = tensor_size->scalar<int32>()();
        OP_REQUIRES_OK(context, ar->set_size(size));
      }
    };
    REGISTER_KERNEL_BUILDER(Name("ArrayContainerSetSize").Device(DEVICE_CPU), ArrayContainerSetSizeOp);

    class ArrayContainerGetOp : public OpKernel {
    public:
      explicit ArrayContainerGetOp(OpKernelConstruction* context) : OpKernel(context) {
        OP_REQUIRES_OK(context, context->GetAttr("T", &dtype_));
      }

      void Compute(OpKernelContext* context) override {
        ArrayContainer* ar;
        OP_REQUIRES_OK(context, GetResourceFromContext(context, "handle", &ar));
        core::ScopedUnref unref(ar);

        const Tensor* tensor_index;
        OP_REQUIRES_OK(context, context->input("index", &tensor_index));
        OP_REQUIRES(context, TensorShapeUtils::IsScalar(tensor_index->shape()),
                    errors::InvalidArgument(
                        "TensorArray index must be scalar, but had shape: ",
                        tensor_index->shape().DebugString()));
        const int32 index = tensor_index->scalar<int32>()();

        PersistentTensor value;
        OP_REQUIRES_OK(context, ar->get(context, index, &value));
        context->set_output(0, *value.AccessTensor(context));
      }

    private:
      DataType dtype_;
    };
    REGISTER_KERNEL_BUILDER(Name("ArrayContainerGet").Device(DEVICE_CPU), ArrayContainerGetOp);

    class ArrayContainerSetOp : public OpKernel {
    public:
      explicit ArrayContainerSetOp(OpKernelConstruction* context) : OpKernel(context) {
        OP_REQUIRES_OK(context, context->GetAttr("T", &dtype_));
      }

      void Compute(OpKernelContext* context) override {
        ArrayContainer* ar;
        OP_REQUIRES_OK(context, GetResourceFromContext(context, "handle", &ar));
        core::ScopedUnref unref(ar);

        const Tensor* tensor_index;
        const Tensor* tensor_value;
        OP_REQUIRES_OK(context, context->input("index", &tensor_index));
        OP_REQUIRES_OK(context, context->input("value", &tensor_value));
    
        OP_REQUIRES(context, TensorShapeUtils::IsScalar(tensor_index->shape()),
                    errors::InvalidArgument(
                        "index must be scalar, but had shape: ",
                        tensor_index->shape().DebugString()));
        const int32 index = tensor_index->scalar<int32>()();
        OP_REQUIRES(context, tensor_value->IsInitialized(), errors::InvalidArgument("value must be initialized"));

        OP_REQUIRES_OK(context, ar->set(context, index, *tensor_value));
      }

    private:
      DataType dtype_;
    };
    REGISTER_KERNEL_BUILDER(Name("ArrayContainerSet").Device(DEVICE_CPU), ArrayContainerSetOp);
  """

  _mod = None

  def __init__(self, dtype, handle=None, container=None, shared_name=None, name="array_container"):
    """
    :param tf.DType dtype:
    :param str container:
    :param str shared_name:
    :param str name:
    :param tf.resource handle: existing handle to reuse. otherwise we will create a new one
    """
    self.dtype = dtype
    if handle is not None:
      self.handle = handle
    else:
      self.handle = self._create(dtype=dtype, container=container, shared_name=shared_name, name=name)

  def __repr__(self):
    return "<%s %r %r>" % (self.__class__.__name__, self.dtype, self.handle)

  @classmethod
  def _make_mod(cls):
    if cls._mod:
      return cls._mod

    # Fix for undefined symbol: _ZN6google8protobuf8internal26fixed_address_empty_stringE.
    # https://github.com/tensorflow/tensorflow/issues/1419
    from google.protobuf.pyext import _message as msg
    lib = msg.__file__
    #lib = "/u/zeyer/.local/lib/python2.7/site-packages/tensorflow/python/_pywrap_tensorflow_internal.so"
    #lib = "/u/zeyer/.local/lib/python2.7/site-packages/tensorflow/contrib/tfprof/python/tools/tfprof/_pywrap_tensorflow_print_model_analysis_lib.so"
    #lib = "/u/zeyer/.local/lib/python2.7/site-packages/google/protobuf/pyext/_message.so"
    #lib = "/u/zeyer/.local/lib/python2.7/site-packages/external/protobuf/python/google/protobuf/pyext/_message.so"

    comp = OpCodeCompiler(
      base_name="TFArrayContainer",
      code_version=1,  # code also ends up in hash, thus this doesn't always needs to be increased
      code=cls.code,
      include_deps=[],
      use_cuda_if_available=False,
      ld_flags=[
        "-Xlinker", "-rpath", "-Xlinker", os.path.dirname(lib),
        "-L", os.path.dirname(lib), "-l", ":" + os.path.basename(lib)])

    mod = comp.load_module()
    cls._mod = mod
    return mod

  def _get_op(self, k):
    mod = self._make_mod()
    from Util import camel_case_to_snake_case
    return getattr(mod, camel_case_to_snake_case(k))

  def _create(self, dtype, container=None, shared_name=None, name="array_container"):
    """
    :param tf.DType dtype:
    :param str container:
    :param str shared_name:
    :param str name:
    :return: handle to ArrayContainer
    :rtype: tf.resource
    """
    op = self._get_op("ArrayContainerCreate")
    return op(T=dtype, container=container, shared_name=shared_name, name=name)

  def get_size(self):
    """
    :return: size int32 scalar
    :rtype: tf.Tensor
    """
    op = self._get_op("ArrayContainerGetSize")
    return op(handle=self.handle)

  def set_size(self, size):
    """
    :param tf.Tensor size:
    :return: operation
    :rtype: tf.Operation
    """
    op = self._get_op("ArrayContainerSetSize")
    return op(handle=self.handle, size=size)

  def get(self, index):
    """
    :param tf.Tensor index: >= 0 and < size
    :return: tensor at that index
    :rtype: tf.Tensor
    """
    op = self._get_op("ArrayContainerGet")
    return op(T=self.dtype, handle=self.handle, index=index)

  def set(self, index, value):
    """
    :param tf.Tensor index: >= 0 and < size
    :param tf.Tensor value:
    :return: operation
    :rtype: tf.Operation
    """
    op = self._get_op("ArrayContainerSet")
    return op(T=self.dtype, handle=self.handle, index=index, value=value)


class ExplicitRandomShuffleQueue(object):
  """
  This is intended to behave very much like tf.RandomShuffleQueue,
  except that it's implemented by other TF native ops / data structures,
  and you can change min_after_dequeue at runtime.
  This means that if you have your own logic about when to end,
  you can set min_after_dequeue=0 and dequeue all the remaining entries from the queue,
  and then later increase min_after_dequeue again.
  You can also start with a small min_after_dequeue and increase the number steadily.
  The original tf.RandomShuffleQueue had the effect of a reset min_after_dequeue=0
  after you closed the queue. However, there was no way to reopen the queue.
  That is the whole reason this implementation exists.

  One difference of this implementation is that you must call the init() op once before usage.

  One way to implement this is in pure TF.
  We need some TF container type which supports having entries of different shapes
  (where the shape can differ where-ever we specified None).
  We also need some TF container which we can access by index.
  tf.TensorArray can handle that.

  Another way to implement this is by multiple stateful tf.py_func which all reference this instance.
  """

  def __init__(self, capacity, min_after_dequeue=0, dtypes=None, shapes=None,
               names=None, seed=None, shared_name=None,
               name="explicit_random_shuffle_queue"):
    """
    :param int capacity:
    :param int|tf.Tensor min_after_dequeue:
    :param list[str|tf.DType] dtypes:
    :param list[tuple[int|tf.Tensor|None]] shapes:
    :param list[str]|None names:
    :param int seed:
    :param str|None shared_name:
    :param str name:
    """
    assert dtypes
    assert not shared_name, "not supported yet"
    assert isinstance(dtypes, list)
    self.dtypes = dtypes
    if shapes is None:
      shapes = [None] * len(dtypes)
    assert isinstance(shapes, list)
    self.shapes = shapes
    assert len(shapes) == len(dtypes)
    if names is not None:
      assert isinstance(names, list)
      assert len(names) == len(dtypes)
    self.names = names
    self._name = name
    self._seed = seed

    with tf.name_scope(self._name):
      self._lock = Lock()
      self._is_full_cond = Condition(lock=self._lock)
      self._min_after_dequeue_cond = Condition(lock=self._lock)

      self.capacity = capacity
      self._min_after_dequeue = tf.Variable(
        initial_value=min_after_dequeue, dtype=tf.int32, trainable=False, name="min_after_dequeue")

      self._is_written = tf.Variable(
        initial_value=tf.zeros(shape=(self.capacity,), dtype=tf.int8), trainable=False, name="free_mask")

      with tf.control_dependencies([self._min_after_dequeue.initializer]):
        self._init_ops = tf.group(self._is_written.initializer)
      self._init_ops = tf.group(
        self._init_ops, self._lock.init(), self._is_full_cond.init(), self._min_after_dequeue_cond.init())

      # TODO Seems like we cannot use tf.TensorArray for what we need here...
      # see test_TensorArray() and https://stackoverflow.com/questions/44418036/
      # Solutions are GlobalTensorArrayOpMaker or TFArrayContainer which also both currently do not work.
      # Thus at the moment, I don't see any good way to make this work...
      self._tas = [
        tf.TensorArray(
          dtype=dtype, size=capacity, clear_after_read=True,
          element_shape=shape, name="%s_TensorArray" % name)
        for (dtype, shape, name) in zip(self.dtypes, self.shapes, self.names or ["unk"] * len(self.dtypes))]
      self._flows = [tf.Variable(initial_value=ta.flow) for ta in self._tas]
      self._init_ops = tf.group(self._init_ops, *[flow.initializer for flow in self._flows])
      assert len(self._tas) == len(self.dtypes)
      self._tas_dict = {name: ta for (name, ta) in zip(self.names, self._tas)} if self.names else None

  def init(self):
    """
    :rtype: tf.Operation
    """
    return self._init_ops

  def size(self):
    """
    :rtype: tf.Tensor
    """
    with reuse_name_scope("%s/size" % self._name):
      return tf.count_nonzero(self._is_written, dtype=tf.int32)

  def min_after_dequeue_read(self):
    return enforce_copy(self._min_after_dequeue.read_value())

  def min_after_dequeue_assign(self, min_after_dequeue):
    """
    :param tf.Tensor min_after_dequeue:
    :rtype: tf.Operation
    """
    with sequential_control_dependencies([
      lambda: self._lock.lock(),
      lambda: self._min_after_dequeue.assign(min_after_dequeue, use_locking=True),
      lambda: self._min_after_dequeue_cond.signal_all(),
      lambda: self._lock.unlock()
    ]):
      return tf.no_op()

  def _get_cur_tensor_array(self, idx):
    ta = self._tas[idx]
    return tf.TensorArray(dtype=ta.dtype, handle=ta.handle, flow=enforce_copy(self._flows[idx].read_value()))

  def _get_cur_tas(self):
    return [self._get_cur_tensor_array(i) for i in range(len(self._tas))]

  def _tas_write(self, index, vs):
    tas = self._get_cur_tas()
    assert len(vs) == len(tas)
    tas_flows = [ta.write(index, v).flow for (ta, v) in zip(tas, vs)]
    return [tf.assign(flow_var, flow) for (flow_var, flow) in zip(self._flows, tas_flows)]

  def _tas_read(self, index):
    tas = self._get_cur_tas()
    return [ta.read(index) for ta in tas]

  def enqueue(self, v):
    """
    :param list[tf.Tensor]|dict[str,tf.Tensor]|tf.Tensor v:
    :rtype: tf.Operation
    """
    if self.names:
      assert isinstance(v, dict)
      v = [v[name] for name in self.names]
    elif not isinstance(v, list) and len(self.dtypes) == 1:
      v = [v]
    assert isinstance(v, list)
    assert len(v) == len(self.dtypes)
    with reuse_name_scope("%s/enqueue" % self._name):
      with tf.control_dependencies([self._lock.lock()]):
        with tf.control_dependencies([self._loop_while_full()]):
          index = tf.cast(tf.arg_min(self._is_written, dimension=0), tf.int32)
          with tf.control_dependencies([tf.scatter_update(self._is_written, index, 1)]):
            with tf.control_dependencies(self._tas_write(index=index, vs=v)):
              with tf.control_dependencies([self._maybe_signal_min_after_dequeue()]):
                return self._lock.unlock()

  def _is_full(self):
    return tf.greater_equal(self.size(), self.capacity)

  def _loop_while_full(self):
    """
    Called with lock held.
    """
    def loop_cond(last):
      with tf.control_dependencies([last]):
        return self._is_full()

    def body(last):
      # This gets only executed if the queue is full. We still have the lock.
      with tf.control_dependencies([last]):
        with tf.control_dependencies([self._is_full_cond.wait()]):
          return tf.identity(last)

    return tf.while_loop(cond=loop_cond, body=body, loop_vars=[0], parallel_iterations=1, back_prop=False)

  def _have_min_after_dequeue(self):
    return tf.greater_equal(self.size(), self._min_after_dequeue)

  def _maybe_signal_min_after_dequeue(self):
    return tf.cond(self._have_min_after_dequeue(), lambda: self._min_after_dequeue_cond.signal(), lambda: tf.no_op())

  def _loop_while_not_min_after_dequeue(self):
    """
    Called with lock held.
    """
    def loop_cond(last):
      with tf.control_dependencies([last]):
        return tf.logical_not(self._have_min_after_dequeue())

    def body(last):
      # This gets only executed if we not have min-after-dequeue. We still have the lock.
      with tf.control_dependencies([last]):
        with tf.control_dependencies([self._min_after_dequeue_cond.wait()]):
          return tf.identity(last)

    return tf.while_loop(cond=loop_cond, body=body, loop_vars=[0], parallel_iterations=1, back_prop=False)

  def dequeue(self):
    with reuse_name_scope("%s/dequeue" % self._name):
      with tf.control_dependencies([self._lock.lock()]):
        with tf.control_dependencies([self._loop_while_not_min_after_dequeue()]):
          free_idxs = tf.cast(tf.where(tf.equal(self._is_written, 1)), tf.int32)  # (num_true, 1)
          free_idxs = tf.random_shuffle(free_idxs, seed=self._seed)
          index = free_idxs[0][0]
          vs = self._tas_read(index)
          with tf.control_dependencies(vs):
            with tf.control_dependencies([tf.scatter_update(self._is_written, index, 0)]):
              with tf.control_dependencies([self._is_full_cond.signal()]):
                with tf.control_dependencies([self._lock.unlock()]):
                  vs = [tf.identity(v) for v in vs]
                  if self.names:
                    return {name: v for (name, v) in zip(self.names, vs)}
                  elif len(vs) == 1:
                    return vs[0]
                  else:
                    return vs
