import getpass
import os
import random
from pathlib import Path

import cv2
import numpy as np

from agrinetdata.ElbitRegisteration import util as elbit_util
from agrinetdata.ElbitRegisteration.Constants import FILE_NAMES
from agrinetdata.ElbitRegisteration.RegParam import RegParam
from agrinetdata.phenomics import Phenomics

ELBIT_SYS_CAMERA_NAME = 'Elbit2'


def transform_ir(img):
    ir = img.astype(np.float32)
    # ir = cv2.resize(ir,(256,256))
    ir = 255.0 * (ir - np.min(ir)) / (np.max(ir) - np.min(ir))
    ir = ir.reshape((*ir.shape, 1))
    ir = np.repeat(ir, 3, -1)
    return ir.astype(np.uint8)


class AgriNetDataLoaderDepth:
    """Wrapper class of Dataset class that performs multi-threaded data loading"""

    def __init__(self, output_dir=None):
        self.ph = Phenomics()
        self.reset()
        self.CNT = 0
        if output_dir is not None:
            self.output_dir = Path(output_dir)
            self.setup_output_dir()

    def reset(self):
        self.elbit2_frame_ids = set()
        self.elbit2_processed_frame_ids_with_uri = {}
        self.elbit2_frame_ids_processed = []
        self.random_to_save = None

    def connect_to_agrinet(self):
        uname = input('Enter username:')
        password = getpass.getpass('Enter password:')
        self.ph.login(uname, password)
        return self

    def load_frames_by_elibt2(self, max_size=-1, shuffle=True, exp_id=None):
        ret = self.ph.get_images_by_experiment_id(exp_id)  # self.ph.get_images_by_camera(ELBIT_SYS_CAMERA_NAME)
        if shuffle:
            random.shuffle(ret)
        for r in ret:
            fid = r['frame_id'] if 'frame_id' in r.keys() else None
            if fid is None:
                print('None FID')
                continue
            self.elbit2_frame_ids.add(fid)
            if fid in self.elbit2_processed_frame_ids_with_uri:
                continue
            if 'image_uri' in r.keys() and '/Processed/' in r['image_uri']:
                frame_uri = r['image_uri'].split('/Processed/')[0]
                if not ((os.path.isfile('{}/{}'.format(frame_uri, 'Calib.txt')) or
                         os.path.isfile('{}/{}'.format(frame_uri, 'Calib.xml'))) and
                        os.path.isfile('{}/{}'.format(frame_uri, FILE_NAMES['RGB'])) and
                        os.path.isfile('{}/{}'.format(frame_uri, FILE_NAMES['IR'])) and
                        os.path.isfile('{}/{}'.format(frame_uri, FILE_NAMES['Depth']))):
                    print('Bad path : {}'.format(frame_uri))
                    continue
                self.elbit2_processed_frame_ids_with_uri[fid] = frame_uri
            else:
                pass  # print('Not Image URI or not Processed')
            if max_size > 0 and len(self.elbit2_processed_frame_ids_with_uri) > max_size:
                break
        print('Number of frames {}'.format(len(self.elbit2_processed_frame_ids_with_uri)))
        self.elbit2_frame_ids_processed = list(self.elbit2_processed_frame_ids_with_uri.keys())
        return self

    def setup_output_dir(self, output_dir=None):
        if output_dir is not None:
            self.output_dir = Path(output_dir)
        if not self.output_dir.exists():
            os.mkdir(self.output_dir.absolute())
            os.mkdir('{}/vis'.format(self.output_dir.absolute()))
            os.mkdir('{}/train'.format(self.output_dir.absolute()))
            os.mkdir('{}/test'.format(self.output_dir.absolute()))
        elif self.output_dir.exists() and not os.listdir(self.output_dir.absolute()):
            return
        else:
            print('Output director exists and not empty. This may cause errors.')
            # answer = input('do you still want to continue ? (y/n):')
            # if answer not in ['y', 'Yes', 'YES', 'Y']:
            #     exit(-1)
            os.rmdir(self.output_dir.absolute())
            os.mkdir(self.output_dir.absolute())

    def get_number_of_processed_frames(self):
        return len(self.elbit2_frame_ids_processed)

    def open_image_with_registeration_by_index(self, index):
        fid = self.elbit2_frame_ids_processed[index]
        return self.open_img_with_registeration_by_fid(fid)

    def open_img_with_registeration_by_fid(self, frame_id):
        frame_uri = self.elbit2_processed_frame_ids_with_uri[frame_id]
        if os.path.isfile('{}/{}'.format(frame_uri, 'Calib.txt')):
            reg_param = RegParam().init_from_txt_file('{}/{}'.format(frame_uri, 'Calib.txt'))
        else:
            reg_param = RegParam().init_from_xml_file('{}/{}'.format(frame_uri, 'Calib.xml'))
        if reg_param is None:
            print('Bad reg_param : {}'.format(frame_uri))
            return None, None
        rgb_img = cv2.imread('{}/{}'.format(frame_uri, FILE_NAMES['RGB']), cv2.IMREAD_UNCHANGED)
        depth_img = cv2.imread('{}/{}'.format(frame_uri, FILE_NAMES['Depth']), cv2.IMREAD_UNCHANGED)
        if depth_img is None or rgb_img is None:
            return None, None
        else:
            distance = elbit_util.calculate_distance(depth_img)
            if distance > 1e-5:
                reg_param.apply_dist_fix(distance)

        reg_rgb_fix = cv2.warpAffine(rgb_img, reg_param.T['Depth'],
                                     dsize=(depth_img.shape[1], depth_img.shape[0]),
                                     flags=cv2.WARP_INVERSE_MAP)
        reg_rgb_fix = cv2.resize(reg_rgb_fix, dsize=None, fx=0.75, fy=0.75, interpolation=cv2.INTER_NEAREST)
        depth_img = cv2.resize(depth_img, dsize=None, fx=0.75, fy=0.75, interpolation=cv2.INTER_NEAREST)
        h, w = depth_img.shape[0:2]
        dw = 384
        dh = 288
        xc, yc = int(w / 2), int(h / 2)

        reg_rgb_fix = reg_rgb_fix[yc - dh // 2:yc + (dh - dh // 2), xc - dw // 2:xc + (dw - dw // 2), :]
        depth_img = depth_img[yc - dh // 2:yc + (dh - dh // 2), xc - dw // 2:xc + (dw - dw // 2)]
        if self.CNT <= 25:
            cv2.imwrite('RGB_{}.jpg'.format(self.CNT), reg_rgb_fix)
            depth_vis = transform_ir(depth_img)
            cv2.imwrite('DEPTH_{}.jpg'.format(self.CNT), depth_vis)
            self.CNT += 1

        return reg_rgb_fix, depth_img

    def load_data_with_registeration(self, max_size=100, thermal_vis=True, testing=0.1):
        for i, frame_id in enumerate(self.elbit2_processed_frame_ids_with_uri.keys()):
            if max_size > 0 and i > max_size:
                break
            dst = 'train' if random.uniform(0, 1) > testing else 'test'
            reg_rgb_fix, depth_image = self.open_img_with_registeration_by_fid(frame_id)
            if reg_rgb_fix is None or depth_image is None:
                print('HMM')
                continue
            cv2.imwrite('{}/{}/{}_{}'.format(self.output_dir.absolute(), dst, frame_id, FILE_NAMES['RGB']), reg_rgb_fix)
            cv2.imwrite('{}/{}/{}_{}'.format(self.output_dir.absolute(), dst, frame_id, FILE_NAMES['Depth']),
                        depth_image)
            if thermal_vis:
                thermal_img_vis = transform_ir(depth_image)
                cv2.imwrite('{}/vis/{}_vis_{}'.format(self.output_dir.absolute(), frame_id, FILE_NAMES['Depth']),
                            thermal_img_vis)


if __name__ == '__main__':
    agrinet_dl = AgriNetDataLoaderDepth('./EXP_523_DEPTH').connect_to_agrinet()
    agrinet_dl.load_frames_by_elibt2(exp_id=523)
    agrinet_dl.load_data_with_registeration(max_size=-1)