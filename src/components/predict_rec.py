import os
import sys
from PIL import Image
__dir__ = os.path.dirname(os.path.abspath(__file__))
sys.path.append(__dir__)
sys.path.insert(0, os.path.abspath(os.path.join(__dir__, '../..')))

os.environ["FLAGS_allocator_strategy"] = 'auto_growth'

import cv2
import numpy as np
import math
import time
import traceback
import paddle
from copy import deepcopy
import src.common.utility as utility
from src.common.ppocr.postprocess import build_post_process
import tritonclient.grpc as grpcclient


class TextRecognizer(object):
    def __init__(self, args):
        self.rec_image_shape = [3,48,320]
        self.rec_batch_num = 6
        postprocess_params = {
            'name': 'CTCLabelDecode',
            "character_dict_path": args.rec_char_dict_path,
            "use_space_char": args.use_space_char
        }
        self.postprocess_op = build_post_process(postprocess_params)

        self.url = '192.168.1.10:8001'
        self.triton_client = grpcclient.InferenceServerClient(url=self.url, verbose=False)
        self.model_name = 'infer_text_rec'
    
    def get_rotate_crop_image(self, img, points):
        assert len(points) == 4, "shape of points must be 4*2"
        img_crop_width = int(
            max(
                np.linalg.norm(points[0] - points[1]),
                np.linalg.norm(points[2] - points[3])))
        img_crop_height = int(
            max(
                np.linalg.norm(points[0] - points[3]),
                np.linalg.norm(points[1] - points[2])))
        pts_std = np.float32([[0, 0], [img_crop_width, 0],
                            [img_crop_width, img_crop_height],
                            [0, img_crop_height]])
        M = cv2.getPerspectiveTransform(points, pts_std)
        dst_img = cv2.warpPerspective(
            img,
            M, (img_crop_width, img_crop_height),
            borderMode=cv2.BORDER_REPLICATE,
            flags=cv2.INTER_CUBIC)
        dst_img_height, dst_img_width = dst_img.shape[0:2]
        if dst_img_height * 1.0 / dst_img_width >= 1.5:
            dst_img = np.rot90(dst_img)
        return dst_img

    def resize_norm_img(self, img, max_wh_ratio):
        imgC, imgH, imgW = self.rec_image_shape
        assert imgC == img.shape[2]
        imgW = int((imgH * max_wh_ratio))
    
        h, w = img.shape[:2]
        ratio = w / float(h)
        if math.ceil(imgH * ratio) > imgW:
            resized_w = imgW
        else:
            resized_w = int(math.ceil(imgH * ratio))
        resized_image = cv2.resize(img, (resized_w, imgH))
        resized_image = resized_image.astype('float32')
        resized_image = resized_image.transpose((2, 0, 1)) / 255
        resized_image -= 0.5
        resized_image /= 0.5
        padding_im = np.zeros((imgC, imgH, imgW), dtype=np.float32)
        padding_im[:, :, 0:resized_w] = resized_image
        return padding_im
    
    def sorted_boxes(self, dt_boxes):
        num_boxes = dt_boxes.shape[0]
        sorted_boxes = sorted(dt_boxes, key=lambda x: (x[0][1], x[0][0]))
        _boxes = list(sorted_boxes)

        for i in range(num_boxes - 1):
            for j in range(i, -1, -1):
                if abs(_boxes[j + 1][0][1] - _boxes[j][0][1]) < 10 and \
                        (_boxes[j + 1][0][0] < _boxes[j][0][0]):
                    tmp = _boxes[j]
                    _boxes[j] = _boxes[j + 1]
                    _boxes[j + 1] = tmp
                else:
                    break
        return _boxes

    def __call__(self, dt_boxes, ori_img):
        dt_boxes_shape = list(dt_boxes.shape)
        images_shape = list(ori_img.shape)
        inputs = []
        outputs = []
        
        inputs.append(grpcclient.InferInput("dt_boxes", dt_boxes_shape, "FP32"))
        inputs.append(grpcclient.InferInput("images", images_shape, "UINT8"))
        inputs[0].set_data_from_numpy(dt_boxes)
        inputs[1].set_data_from_numpy(ori_img)

        outputs.append(grpcclient.InferRequestedOutput("pre_rec_output"))

        results = self.triton_client.infer(model_name='pre_text_rec', inputs=inputs, outputs=outputs)
        
        pre_rec_output = results.as_numpy("pre_rec_output")

        pre_rec_output_shape = list(pre_rec_output.shape)
        
        inputs = []
        outputs = []
        
        inputs.append(grpcclient.InferInput("x", pre_rec_output_shape, "FP32"))
        inputs[0].set_data_from_numpy(pre_rec_output)

        outputs.append(grpcclient.InferRequestedOutput("softmax_2.tmp_0"))

        results = self.triton_client.infer(model_name=self.model_name, inputs=inputs, outputs=outputs)
        
        infer_text_rec_output = results.as_numpy("softmax_2.tmp_0")
            
        preds = infer_text_rec_output

        rec_result = self.postprocess_op(preds)
        
        return rec_result




