from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import csv
from logging import getLogger, StreamHandler, DEBUG, INFO

import os
import time

import numpy as np

import cv2
from caffe import Layer


class ImageTransformer(object):

    logger = getLogger('ImageTransformer')
    handler = StreamHandler()
    handler.setLevel(INFO)
    logger.setLevel(INFO)
    logger.addHandler(handler)

    @staticmethod
    def get_params(
            height=-1, width=-1, crop_size=None, pad=0, mirror=False,
            scale=None, mean_value=None, color=True, channel_swap=None,
            random_crop=False, random_seed=313):
        """"""
        return locals()

    def __init__(self, param):
        self.random_seed_ = param.get('random_seed', 313)
        self.mirror_ = param.get('mirror', False)
        self.crop_size_ = param.get('crop_size', None)
        self.random_crop_ = param.get('random_crop', False)
        self.mean_value_ = param.get('mean_value', None)
        self.scale_ = param.get('scale', None)
        self.color_ = param.get('color', True)
        self.pad_ = param.get('pad', 0)
        self.height_ = param.get('height', -1)
        self.width_ = param.get('width', -1)
        self.channel_swap_ = param.get('channel_swap', None)
        self.rng_mirror_ = np.random.RandomState(self.random_seed_)
        self.rng_crop_ = np.random.RandomState(self.random_seed_ + 1)
        self.logger.info(
            (os.linesep + '    ').join(
                map(lambda kv: "%s = %s" % (kv[0], kv[1]),
                    filter(lambda kv: kv[0] in (
                        'random_seed_', 'mirror_', 'crop_size_', 'mean_value_',
                        'scale_', 'color_', 'pad_', 'height_', 'width_',
                        'channel_swap_', 'random_crop_'),
                    self.__dict__.iteritems()))))
        c = 3 if self.color_ else 1
        if self.crop_size_ is None:
            h = self.height_
            w = self.width_
        else:
            try:
                h, w = self.crop_size_
            except:
                h = w = self.crop_size_
        self.out_shape_ = (c, h, w)

    @property
    def out_shape(self):
        return self.out_shape_

    def transform(self, img, rng_crop=None, rng_mirror=None):
        """"""
        # To support multi process
        if rng_crop is None:
            rng_crop = self.rng_crop_
        if rng_mirror is None:
            rng_mirror = self.rng_mirror_

        ts = time.clock()
        if self.height_ > 0 and self.width_ > 0:
            img = cv2.resize(img, (self.width_, self.height_))
            self.logger.debug("transform resize")
        # PAD
        if self.pad_:
            try:
                to, bo, le, ri = self.pad_
            except:
                try:
                    tobo, leri = self.pad_
                    to = bo = tobo
                    le = ri = leri
                except:
                    to = bo = le = ri = self.pad_
            img = cv2.copyMakeBorder(img, to, bo, le, ri,
                                     borderType=cv2.BORDER_REFLECT)
            self.logger.debug("transform pad")
        # CROP
        if self.crop_size_ is not None:
            try:
                hcrop, wcrop = self.crop_size_
            except:
                hcrop = wcrop = self.crop_size_
            h, w, _ = img.shape
            if h != hcrop or w != wcrop:
                if self.random_crop_:
                    hoff = rng_crop.randint(0, h - hcrop + 1)
                    woff = rng_crop.randint(0, w - wcrop + 1)
                    self.logger.debug(
                        "transform crop random (%d, %d)" % (hoff, woff))
                else:
                    hoff = (h - hcrop) / 2
                    woff = (w - wcrop) / 2
                    self.logger.debug("transform crop")
                img = img[hoff:hoff + hcrop, woff:woff + wcrop]
        # MIRROR
        if self.mirror_:
            if rng_mirror.randint(0, 2):
                img = img[:, ::-1]
                self.logger.debug("transform mirror")
        # COLOR
        if not self.color_:
            img = img.mean(2)[..., np.newaxis]
            self.logger.debug("transform color")
        # FLOAT
        img = img.astype('float32')
        # SUBTRACT
        if self.mean_value_ is not None:
            if len(self.mean_value_) in (1, 3):
                img -= self.mean_value_
                self.logger.debug("transform mean")
            else:
                raise ValueError("mean_value is invalid")
        # SCALE INTENSITY
        if self.scale_ is not None:
            img *= self.scale_
            self.logger.debug("transform scale intensity")
        # CHANNEL SWAP
        if self.channel_swap_:
            img = img[:, :, self.channel_swap_]
            self.logger.debug("transform channel swap")
        # TRANSPOSE
        img = img.transpose(2, 0, 1)
        # Time
        self.logger.debug(
            'transform takes {} ms'.format(1000 * (time.clock() - ts)))
        return img


class BaseDataLayer(Layer):

    def setup(self, bottom, top):
        param = eval(self.param_str_)
        self.batch_size_ = param['batch_size']
        self.data_setup(bottom, top)
        top[0].reshape(*self.data_.shape)
        self.executor_ = ThreadPoolExecutor(max_workers=1)
        self.thread_ = self.executor_.submit(self.internal_thread_entry)

    def reshape(self, bottom, top):
        pass

    def forward(self, bottom, top):
        self.thread_.result()
        top[0].reshape(*self.data_.shape)
        top[0].data[...] = self.data_
        self.thread_ = self.executor_.submit(self.internal_thread_entry)

    def data_setup(self, bottom, top):
        raise NotImplementedError()

    def internal_thread_entry(self):
        raise NotImplementedError()

    def __del__(self):
        self.thread_.result()
        self.executor_.shutdown()
        super(self.__class__, self).__del__()


def _process_load_image(args):
    try:
        path_img, transformer, rng_crop, rng_mirror = args
    except:
        path_img, transformer = args
        rng_crop, rng_mirror = None, None
    img = cv2.imread(path_img)
    if img is None:
        raise ValueError("File not exists or corrupted: %s" % path_img)
    return transformer.transform(img, rng_crop=rng_crop, rng_mirror=rng_mirror)


class ImageDataLayer(BaseDataLayer):

    logger = getLogger('ImageDataLayer')
    handler = StreamHandler()
    handler.setLevel(INFO)
    logger.setLevel(INFO)
    logger.addHandler(handler)

    @staticmethod
    def get_params(
            source, column_id=0, root='', shuffle=False, num_thread=8,
            height=-1, width=-1, crop_size=None, pad=0, mirror=False,
            scale=None, mean_value=None, color=True, channel_swap=None,
            random_crop=False, random_seed=313):
        """"""
        return locals()

    def data_setup(self, bottom, top):
        param = eval(self.param_str_)
        self.source_ = param['source']
        self.column_id_ = param.get('column_id_', 0)
        self.root_ = param.get('root', '')
        self.shuffle_ = param.get('shuffle', False)
        self.random_seed_ = param.get('random_seed', 313)
        self.num_thread_ = param.get('num_thread', 8)
        with open(self.source_, 'r') as fd:
            self.lines_ = filter(
                lambda x: x,
                map(lambda x: x[self.column_id_].strip(), csv.reader(fd)))
        if not len(self.lines_):
            raise ValueError("Dataset is empty.")
        self.indexes_ = np.arange(len(self.lines_))
        self.at_ = 0
        if self.shuffle_:
            self.rng_ = np.random.RandomState(self.random_seed_)
            self.rng_.shuffle(self.indexes_)
        self.transformer_ = ImageTransformer(param)
        self.data_ = np.zeros(
            (self.batch_size_,) + self.transformer_.out_shape, 'float32')

    def internal_thread_entry(self):
        # Batch images
        tsw = time.clock()
        images = ()
        for i in xrange(self.batch_size_):
            try:
                index = self.indexes_[self.at_]
                self.at_ += 1
            except IndexError:
                if self.shuffle_:
                    self.rng_.shuffle(self.indexes_)
                self.at_ = 0
                index = self.indexes_[self.at_]
                self.at_ += 1
            images += self.root_ + self.lines_[index],
        # Load images in parallel
        """
        with ProcessPoolExecutor(self.num_thread_) as executor:
            rng_crop = [
                np.random.RandomState(seed)
                for seed in self.transformer_.rng_crop_.randint(
                    0, 2**32-1, (len(images), 2))]
            rng_mirror = [
                np.random.RandomState(seed)
                for seed in self.transformer_.rng_mirror_.randint(
                    0, 2**32-1, (len(images), 2))]
            for index, img in enumerate(executor.map(
                _process_load_image,
                zip(images, [self.transformer_] * len(images),
                    rng_crop, rng_mirror)
            )):
                self.data_[index] = img
        """
        for index, img in enumerate(images):
            self.data_[index] = _process_load_image((img, self.transformer_))
        self.logger.debug(
            'read a batch takes {} ms'.format(1000 * (time.clock() - tsw)))


class HDF5Layer(BaseDataLayer):

    def data_setup(self, bottom, top):
        import h5py
        param = eval(self.param_str_)
        self.source_ = param['source']
        self.path_h5_ = param['path_h5']
        self.column_id_ = param.get('column_id_', 0)
        self.shuffle_ = param.get('shuffle', False)
        self.random_seed_ = param.get('random_seed', 313)
        self.blob_name_ = param['blob_name']

        with open(self.source_, 'r') as fd:
            self.lines_ = filter(
                lambda x: x,
                map(lambda x: x[self.column_id_].strip(), csv.reader(fd)))
        if not len(self.lines_):
            raise ValueError("Dataset is empty.")
        self.indexes_ = np.arange(len(self.lines_))
        self.at_ = 0
        if self.shuffle_:
            self.rng_ = np.random.RandomState(self.random_seed_)
            self.rng_.shuffle(self.indexes_)
        self.hd_ = h5py.File(self.path_h5_, 'r')
        self.data_ = np.zeros(
            (self.batch_size_,)
            + self.hd_[self.lines_[0]][self.blob_name_].shape, 'float32')

    def __del__(self):
        self.hd_.close()
        super(HDF5Layer, self).__del__()

    def internal_thread_entry(self):
        # Batch images
        for i in xrange(self.batch_size_):
            try:
                index = self.indexes_[self.at_]
                self.at_ += 1
            except IndexError:
                if self.shuffle_:
                    self.rng_.shuffle(self.indexes_)
                self.at_ = 0
                index = self.indexes_[self.at_]
                self.at_ += 1
            self.data_[i] = self.hd_[self.lines_[index]][self.blob_name_].value


class ScalarDataLayer(BaseDataLayer):
    logger = getLogger('ScalarDataLayer')
    handler = StreamHandler()
    handler.setLevel(INFO)
    logger.setLevel(INFO)
    logger.addHandler(handler)

    @staticmethod
    def get_params(
            source, column_id=0, shuffle=False, random_seed=313):
        """"""
        return locals()

    def data_setup(self, bottom, top):
        param = eval(self.param_str_)
        self.source_ = param['source']
        self.column_id_ = param.get('column_id', 0)
        self.shuffle_ = param.get('shuffle', False)
        self.random_seed_ = param.get('random_seed', 313)
        with open(self.source_, 'r') as fd:
            self.values_ = filter(
                lambda x: x,
                map(lambda x: x[self.column_id_].strip(), csv.reader(fd)))
            self.values_ = np.array(map(float, self.values_), dtype='float32')
        if self.values_.size == 0:
            raise ValueError("Dataset is empty.")
        self.indexes_ = np.arange(self.values_.size)
        self.at_ = 0
        if self.shuffle_:
            self.rng_ = np.random.RandomState(self.random_seed_)
            self.rng_.shuffle(self.indexes_)
        self.data_ = np.zeros(
            (self.batch_size_,) + (1,) * 3, 'float32')

    def internal_thread_entry(self):
        indexes = self.indexes_[self.at_:self.at_ + self.batch_size_]
        self.at_ += self.batch_size_
        if indexes.shape[0] != self.batch_size_:
            if self.shuffle_:
                self.rng_.shuffle(self.indexes_)
            res = self.batch_size_ - indexes.shape[0]
            indexes2 = self.indexes_[:res]
            indexes = np.concatenate((indexes, indexes2))
            self.at_ = res
        self.data_[:, 0, 0, 0] = self.values_[indexes]
