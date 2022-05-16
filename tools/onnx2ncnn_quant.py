# Copyright (c) OpenMMLab. All rights reserved.
import argparse
import logging

from mmcv import Config

from mmdeploy.utils import get_root_logger, load_config


def get_table(onnx_path: str,
              deploy_cfg: Config,
              model_cfg: Config,
              output_onnx_path: str,
              output_quant_table_path: str,
              image_dir: str = None,
              device: str = 'cuda',
              dataset_type: str = 'val'):

    input_shape = None
    # setup input_shape if existed in `onnx_config`
    if 'onnx_config' in deploy_cfg and 'input_shape' in deploy_cfg.onnx_config:
        input_shape = deploy_cfg.onnx_config.input_shape

    # build calibration dataloader. If img dir not specified, use val dataset.
    if image_dir is not None:
        from quant_image_dataset import QuantizationImageDataset
        from torch.utils.data import DataLoader
        dataset = QuantizationImageDataset(
            path=image_dir, deploy_cfg=deploy_cfg, model_cfg=model_cfg)
        dataloader = DataLoader(dataset, batch_size=1)
    else:
        from mmdeploy.apis.utils import build_task_processor
        task_processor = build_task_processor(model_cfg, deploy_cfg, device)
        dataset = task_processor.build_dataset(model_cfg, dataset_type)
        dataloader = task_processor.build_dataloader(dataset, 1, 1)

    if input_shape is None:
        # get an available input shape randomly
        for _, input_data in enumerate(dataloader):
            input_shape = input_data['img'].shape
            break

    from ppq import QuantizationSettingFactory, TargetPlatform
    from ppq.api import export_ppq_graph, quantize_onnx_model

    # settings for ncnn quantization
    quant_setting = QuantizationSettingFactory.default_setting()
    quant_setting.equalization = False
    quant_setting.dispatcher = 'conservative'

    # quantize the model
    quantized = quantize_onnx_model(
        onnx_import_file=onnx_path,
        calib_dataloader=dataloader,
        calib_steps=max(4, min(512, len(dataset))),
        input_shape=input_shape,
        setting=quant_setting,
        collate_fn=lambda x: x['img'].to(device),
        platform=TargetPlatform.NCNN_INT8,
        device=device,
        verbose=1)

    # export quantized graph and quant table
    export_ppq_graph(
        graph=quantized,
        platform=TargetPlatform.NCNN_INT8,
        graph_save_to=output_onnx_path,
        config_save_to=output_quant_table_path)
    return


def parse_args():
    parser = argparse.ArgumentParser(description='Convert ONNX to ncnn.')
    parser.add_argument('onnx_path', help='ONNX model path')
    parser.add_argument('deploy_cfg_path', help='Input deploy config path')
    parser.add_argument('model_cfg_path', help='Input model config path')
    parser.add_argument('quant_onnx_path', help='Output onnx path')
    parser.add_argument('quant_table_path', help='Output quant table path')
    parser.add_argument(
        '--image_dir',
        type=str,
        default=None,
        help='Calibraion Image Directory.')
    parser.add_argument(
        '--log-level',
        help='set log level',
        default='INFO',
        choices=list(logging._nameToLevel.keys()))
    args = parser.parse_args()

    return args


def main():
    args = parse_args()
    logger = get_root_logger(log_level=args.log_level)

    onnx_path = args.onnx_path
    deploy_cfg, model_cfg = load_config(args.deploy_cfg_path,
                                        args.model_cfg_path)
    quant_table_path = args.quant_table_path
    quant_onnx_path = args.quant_onnx_path
    image_dir = args.image_dir

    logger.info(f'onnx2ncnn_quant: \n\tonnx_path: {onnx_path} ')
    try:
        get_table(onnx_path, deploy_cfg, model_cfg, quant_onnx_path,
                  quant_table_path, image_dir)
        logger.info('onnx2ncnn_quant success.')
    except Exception as e:
        logger.error(e)
        logger.error('onnx2ncnn_quant failed.')


if __name__ == '__main__':
    main()
