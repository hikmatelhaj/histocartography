"""Unit test for interpretability.saliency_explainer.graph_gradcam_explainer with tissue graphs"""
import unittest
import numpy as np
import cv2
import torch
import yaml
import os 
from copy import deepcopy
import h5py
import dgl 
from dgl.data.utils import load_graphs

from histocartography.interpretability.saliency_explainer.graph_gradcam_explainer import GraphGradCAMExplainer
from histocartography.utils.graph import set_graph_on_cuda
from histocartography.utils.io import load_image
from histocartography.preprocessing.superpixel import ColorMergedSuperpixelExtractor
from histocartography.visualisation.graph_visualization import GraphVisualization

BASE_S3 = 's3://mlflow/'
IS_CUDA = torch.cuda.is_available()


class GraphGradCAMTGTestCase(unittest.TestCase):
    """GraphGradCAMTGTestCaseadCAMTestCase class."""

    def setUp(self):
        """Setting up the test."""

    def explain(self):
        """Test Graph GradCAM to explain a Tissue Graph.
        """

        # 1. load a tissue graph and image 
        base_path = '../data'
        image_fnames = ['283_dcis_4.png', '1238_adh_10.png', '1286_udh_35.png', '1937_benign_4.png', '311_fea_25.png']
        tg_fnames = ['283_dcis_4_tg.bin', '1238_adh_10_tg.bin', '1286_udh_35_tg.bin', '1937_benign_4_tg.bin', '311_fea_25_tg.bin']
        os.makedirs(os.path.join(base_path, 'visualization'), exist_ok=True)
        os.makedirs(os.path.join(base_path, 'explainers'), exist_ok=True)

        for image_name, tg_name in zip(image_fnames, tg_fnames):
            print('*** Testing tissue graph explainer {}'.format(tg_name))

            image = np.array(load_image(os.path.join(base_path, 'images', image_name)))

            tissue_graph, _ = load_graphs(os.path.join(base_path, 'tissue_graphs', tg_name))
            tissue_graph = tissue_graph[0]
            tissue_graph.ndata['feat'] = torch.cat(
                (tissue_graph.ndata['feat'].float(),
                (tissue_graph.ndata['centroid'] / torch.FloatTensor(image.shape[:-1])).float()),
                dim=1
            )
            if IS_CUDA:
                tissue_graph = set_graph_on_cuda(tissue_graph)

            # 2. run the explainer
            explainer = GraphGradCAMExplainer(
                model_path=BASE_S3 + 'a47b5c2fdf4d49b388e67b63c3c7a8fc/artifacts/model_best_val_weighted_f1_score_0'  
            )
            importance_scores, logits = explainer.process(tissue_graph)

            # 3. print output
            print('Number of nodes:', tissue_graph.number_of_nodes())
            print('Number of edges:', tissue_graph.number_of_edges())
            print('Node features:', tissue_graph.ndata['feat'].shape)
            print('Node centroids:', tissue_graph.ndata['centroid'].shape)
            print('Logits:', logits.shape)
            print('Prediction: [Normal, Benign, Atypical, DCIS, Invasive]', logits.squeeze())
            print('Importance scores:', importance_scores)

            # 4. save as h5 file
            with h5py.File(os.path.join(base_path, 'explainers', image_name.replace('.png', '_tg_importance.h5')), 'w') as hf:
                hf.create_dataset("importance",  data=importance_scores)

            # 5. detect super pixels for visualization  
            superpixel_detector = ColorMergedSuperpixelExtractor(
                nr_superpixels=200,
                downsampling_factor=8,
                compactness=20,
                blur_kernel_size=1
            )
            merged_superpixels, _, _ = superpixel_detector.process(image)

            # 6. visualize the tissue graph
            visualiser = GraphVisualization(
                show_centroid=False,
                show_edges=False
            )
            out = visualiser.process(
                image=image,
                graph=tissue_graph,
                node_importance=importance_scores,
                instance_map=merged_superpixels
            )
            tg_fname = image_name.replace('.png', '_tg.png')
            out.save(os.path.join(base_path, 'visualization', tg_fname))

    def tearDown(self):
        """Tear down the tests."""


if __name__ == "__main__":
    model = GraphGradCAMTGTestCase()
    model.explain()