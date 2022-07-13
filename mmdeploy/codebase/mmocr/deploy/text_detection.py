# Copyright (c) OpenMMLab. All rights reserved.
from typing import Any, Dict, Optional, Sequence, Tuple, Union

import mmcv
import numpy as np
import torch
from mmcv.parallel import DataContainer, collate, scatter
from mmdet.datasets import replace_ImageToTensor
from torch import nn
from torch.utils.data import Dataset

from mmdeploy.codebase.base import BaseTask
from mmdeploy.utils import Task, get_input_shape
from .mmocr import MMOCR_TASK


def process_model_config(model_cfg: mmcv.Config,
                         imgs: Union[Sequence[str], Sequence[np.ndarray]],
                         input_shape: Optional[Sequence[int]] = None):
    """Process the model config.

    Args:
        model_cfg (mmcv.Config): The model config.
        imgs (Sequence[str] | Sequence[np.ndarray]): Input image(s), accepted
            data type are List[str], List[np.ndarray].
        input_shape (list[int]): A list of two integer in (width, height)
            format specifying input shape. Default: None.

    Returns:
        mmcv.Config: the model config after processing.
    """
    if model_cfg.data.test['type'] == 'ConcatDataset':
        model_cfg.data.test.pipeline = \
            model_cfg.data.test['datasets'][0].pipeline

    is_ndarray = isinstance(imgs[0], np.ndarray)

    if is_ndarray:
        model_cfg.data.test.pipeline[0].type = 'LoadImageFromNdarray'

    test_pipeline = model_cfg.data.test.pipeline
    test_pipeline = replace_ImageToTensor(test_pipeline)
    # for static exporting
    if input_shape is not None:
        test_pipeline[1].img_scale = tuple(input_shape)
        test_pipeline[1].transforms[0].keep_ratio = False
        test_pipeline[1].transforms[0].img_scale = tuple(input_shape)
    model_cfg.data.test.pipeline = test_pipeline
    return model_cfg


@MMOCR_TASK.register_module(Task.TEXT_DETECTION.value)
class TextDetection(BaseTask):
    """Text detection task class.

    Args:
        model_cfg (mmcv.Config): Loaded model Config object..
        deploy_cfg (mmcv.Config): Loaded deployment Config object.
        device (str): A string represents device type.
    """

    def __init__(self, model_cfg: mmcv.Config, deploy_cfg: mmcv.Config,
                 device: str):
        super(TextDetection, self).__init__(model_cfg, deploy_cfg, device)

    def init_backend_model(self,
                           model_files: Optional[str] = None,
                           **kwargs) -> torch.nn.Module:
        """Initialize backend model.

        Args:
            model_files (Sequence[str]): Input model files.

        Returns:
            nn.Module: An initialized backend model.
        """
        from .text_detection_model import build_text_detection_model
        model = build_text_detection_model(
            model_files, self.model_cfg, self.deploy_cfg, device=self.device, **kwargs)
        return model.eval()

    def init_pytorch_model(self,
                           model_checkpoint: Optional[str] = None,
                           cfg_options: Optional[Dict] = None,
                           **kwargs) -> torch.nn.Module:
        """Initialize torch model.

        Args:
            model_checkpoint (str): The checkpoint file of torch model,
                defaults to `None`.
            cfg_options (dict): Optional config key-pair parameters.

        Returns:
            nn.Module: An initialized torch model generated by OpenMMLab
                codebases.
        """
        from mmcv.cnn.utils import revert_sync_batchnorm
        from mmocr.apis import init_detector
        model = init_detector(self.model_cfg, model_checkpoint, self.device,
                              cfg_options)
        model = revert_sync_batchnorm(model)

        return model.eval()

    def create_input(self,
                     imgs: Union[str, np.ndarray],
                     input_shape: Sequence[int] = None) \
            -> Tuple[Dict, torch.Tensor]:
        """Create input for segmentor.

        Args:
            imgs (str | np.ndarray): Input image(s), accepted data type are
            `str`, `np.ndarray`.
            input_shape (list[int]): A list of two integer in (width, height)
                format specifying input shape. Defaults to `None`.

        Returns:
            tuple: (data, img), meta information for the input image and input.
        """
        if isinstance(imgs, (list, tuple)):
            if not isinstance(imgs[0], (np.ndarray, str)):
                raise AssertionError('imgs must be strings or numpy arrays')

        elif isinstance(imgs, (np.ndarray, str)):
            imgs = [imgs]
        else:
            raise AssertionError('imgs must be strings or numpy arrays')
        cfg = process_model_config(self.model_cfg, imgs, input_shape)
        from mmdet.datasets.pipelines import Compose
        from mmocr.datasets import build_dataset  # noqa: F401
        test_pipeline = Compose(cfg.data.test.pipeline)

        data_list = []
        for img in imgs:
            # prepare data
            if isinstance(imgs[0], np.ndarray):
                # directly add img
                data = dict(img=img)
            else:
                # add information into dict
                data = dict(img_info=dict(filename=img), img_prefix=None)

            # build the data pipeline
            data = test_pipeline(data)
            # get tensor from list to stack for batch mode (text detection)
            data_list.append(data)

        if isinstance(data_list[0]['img'], list) and len(data_list) > 1:
            raise Exception('aug test does not support '
                            f'inference with batch size '
                            f'{len(data_list)}')

        data = collate(data_list, samples_per_gpu=len(imgs))

        # process img_metas
        if isinstance(data['img_metas'], list):
            data['img_metas'] = [
                img_metas.data[0] for img_metas in data['img_metas']
            ]
        else:
            data['img_metas'] = data['img_metas'].data

        if isinstance(data['img'], list):
            data['img'] = [img.data for img in data['img']]
            if isinstance(data['img'][0], list):
                data['img'] = [img[0] for img in data['img']]
        else:
            data['img'] = data['img'].data

        if self.device != 'cpu':
            data = scatter(data, [self.device])[0]

        return data, data['img']

    def visualize(self,
                  model: nn.Module,
                  image: Union[str, np.ndarray],
                  result: list,
                  output_file: str,
                  window_name: str = '',
                  show_result: bool = False):
        """Visualize predictions of a model.

        Args:
            model (nn.Module): Input model.
            image (str | np.ndarray): Input image to draw predictions on.
            result (list): A list of predictions.
            output_file (str): Output file to save drawn image.
            window_name (str): The name of visualization window. Defaults to
                an empty string.
            show_result (bool): Whether to show result in windows, defaults
                to `False`.
        """
        show_img = mmcv.imread(image) if isinstance(image, str) else image
        output_file = None if show_result else output_file
        model.show_result(
            show_img,
            result,
            out_file=output_file,
            win_name=window_name,
            show=show_result)

    @staticmethod
    def run_inference(model: nn.Module,
                      model_inputs: Dict[str, torch.Tensor]) -> list:
        """Run inference once for a segmentation model of mmseg.

        Args:
            model (nn.Module): Input model.
            model_inputs (dict): A dict containing model inputs tensor and
                meta info.

        Returns:
            list: The predictions of model inference.
        """
        return model(**model_inputs, return_loss=False, rescale=True)

    @staticmethod
    def get_partition_cfg(partition_type: str) -> Dict:
        """Get a certain partition config.

        Args:
            partition_type (str): A string specifying partition type.

        Returns:
            dict: A dictionary of partition config.
        """
        raise NotImplementedError('Not supported yet.')

    @staticmethod
    def get_tensor_from_input(input_data: Dict[str, Any]) -> torch.Tensor:
        """Get input tensor from input data.

        Args:
            input_data (dict): Input data containing meta info and image
                tensor.
        Returns:
            torch.Tensor: An image in `Tensor`.
        """
        if isinstance(input_data['img'], DataContainer):
            return input_data['img'].data[0]
        return input_data['img'][0]

    @staticmethod
    def evaluate_outputs(model_cfg,
                         outputs: Sequence,
                         dataset: Dataset,
                         metrics: Optional[str] = None,
                         out: Optional[str] = None,
                         metric_options: Optional[dict] = None,
                         format_only: bool = False,
                         log_file: Optional[str] = None):
        """Perform post-processing to predictions of model.

        Args:
            outputs (Sequence): A list of predictions of model inference.
            dataset (Dataset): Input dataset to run test.
            model_cfg (mmcv.Config): The model config.
            metrics (str): Evaluation metrics, which depends on
                the codebase and the dataset, e.g., "acc" for text
                recognition, and "hmean-iou" for text detection.
            out (str): Output result file in pickle format, defaults to `None`.
            metric_options (dict): Custom options for evaluation, will be
                kwargs for dataset.evaluate() function. Defaults to `None`.
            format_only (bool): Format the output results without perform
                evaluation. It is useful when you want to format the result
                to a specific format and submit it to the test server. Defaults
                to `False`.
            log_file (str | None): The file to write the evaluation results.
                Defaults to `None` and the results will only print on stdout.
        """
        from mmcv.utils import get_logger
        logger = get_logger('test', log_file=log_file)

        if out:
            logger.debug(f'writing results to {out}')
            mmcv.dump(outputs, out)
        kwargs = {} if metric_options is None else metric_options
        if format_only:
            dataset.format_results(outputs, **kwargs)
        if metrics:
            eval_kwargs = model_cfg.get('evaluation', {}).copy()
            # hard-code way to remove EvalHook args
            for key in [
                    'interval', 'tmpdir', 'start', 'gpu_collect', 'save_best',
                    'rule'
            ]:
                eval_kwargs.pop(key, None)
            eval_kwargs.update(dict(metric=metrics, **kwargs))
            logger.info(dataset.evaluate(outputs, **eval_kwargs))

    def get_preprocess(self) -> Dict:
        """Get the preprocess information for SDK.

        Return:
            dict: Composed of the preprocess information.
        """
        input_shape = get_input_shape(self.deploy_cfg)
        model_cfg = process_model_config(self.model_cfg, [''], input_shape)
        preprocess = model_cfg.data.test.pipeline
        return preprocess

    def get_postprocess(self) -> Dict:
        """Get the postprocess information for SDK.

        Return:
            dict: Composed of the postprocess information.
        """
        postprocess = self.model_cfg.model.bbox_head
        return postprocess

    def get_model_name(self) -> str:
        """Get the model name.

        Return:
            str: the name of the model.
        """
        assert 'type' in self.model_cfg.model, 'model config contains no type'
        name = self.model_cfg.model.type.lower()
        return name
