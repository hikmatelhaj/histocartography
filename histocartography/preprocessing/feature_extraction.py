"""Extract features from images for a given structure"""

import math
import warnings
from abc import abstractmethod
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch
import torchvision
from histocartography.utils import dynamic_import_from
from PIL import Image
from scipy.stats import skew
from skimage.feature import greycomatrix, greycoprops
from skimage.filters.rank import entropy as Entropy
from skimage.measure import regionprops
from skimage.measure._regionprops import _RegionProperties
from skimage.morphology import disk
from torch import nn
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms

from .pipeline import PipelineStep


class FeatureExtractor(PipelineStep):
    """Base class for feature extraction"""

    def process(
        self, input_image: np.ndarray, instance_map: np.ndarray
    ) -> torch.Tensor:
        """Extract features from the input_image for the defined instance_map

        Args:
            input_image (np.array): Original RGB image
            instance_map (np.array): Extracted instance_map

        Returns:
            torch.Tensor: Extracted features
        """
        return self._extract_features(input_image, instance_map)

    @abstractmethod
    def _extract_features(
        self, input_image: np.ndarray, instance_map: np.ndarray
    ) -> torch.Tensor:
        """Extract features from the input_image for the defined structure

        Args:
            input_image (np.array): Original RGB image
            structure (np.array): Structure to extract features

        Returns:
            torch.Tensor: Extracted features
        """


class HandcraftedFeatureExtractor(FeatureExtractor):
    """Helper class to extract handcrafted features from instance maps"""

    @staticmethod
    def _color_features_per_channel(img_rgb_ch, img_rgb_sq_ch, mask_idx, mask_size):
        codes = img_rgb_ch[mask_idx[0], mask_idx[1]].ravel()
        hist, _ = np.histogram(codes, bins=np.arange(0, 257, 32))  # 8 bins
        feats_ = list(hist / mask_size)
        color_mean = np.mean(codes)
        color_std = np.std(codes)
        color_median = np.median(codes)
        color_skewness = skew(codes)

        codes = img_rgb_sq_ch[mask_idx[0], mask_idx[1]].ravel()
        color_energy = np.mean(codes)

        feats_.append(color_mean)
        feats_.append(color_std)
        feats_.append(color_median)
        feats_.append(color_skewness)
        feats_.append(color_energy)
        return feats_

    def _extract_features(
        self, input_image: np.ndarray, instance_map: np.ndarray
    ) -> torch.Tensor:
        """Extract handcrafted features from the input_image in the defined instance_map regions

        Args:
            input_image (np.array): Original RGB Image
            instance_map (np.array): Extracted instance_map. Different regions have different int values,
                                     the background is defined to have value 0 and is ignored.

        Returns:
            torch.Tensor: Extracted shape, color and texture features:
                          Shape:   area, convex_area, eccentricity, equivalent_diameter, euler_number, extent, filled_area,
                                   major_axis_length, minor_axis_length, orientation, perimiter, solidity;
                          Color:   Per channel (RGB) histogram with 8 bins:
                                   mean, std, median, skewness, energy;
                          Texture: entropy, glcm_contrast, glcm_dissililarity, glcm_homogeneity, glcm_energy, glcm_ASM
                                   (glcm = grey-level co-occurance matrix);
        """
        node_feat = []

        img_gray = cv2.cvtColor(input_image, cv2.COLOR_RGB2GRAY)
        img_square = np.square(input_image)

        img_entropy = Entropy(img_gray, disk(3))

        # For each super-pixel
        regions = regionprops(instance_map)

        for _, region in enumerate(regions):
            sp_mask = np.array(instance_map == region["label"], np.uint8)
            sp_rgb = cv2.bitwise_and(input_image, input_image, mask=sp_mask)
            sp_gray = img_gray * sp_mask
            mask_size = np.sum(sp_mask)
            mask_idx = np.where(sp_mask != 0)

            # Compute using mask [12 features]
            area = region["area"]
            convex_area = region["convex_area"]
            eccentricity = region["eccentricity"]
            equivalent_diameter = region["equivalent_diameter"]
            euler_number = region["euler_number"]
            extent = region["extent"]
            filled_area = region["filled_area"]
            major_axis_length = region["major_axis_length"]
            minor_axis_length = region["minor_axis_length"]
            orientation = region["orientation"]
            perimeter = region["perimeter"]
            solidity = region["solidity"]
            feats_shape = [
                area,
                convex_area,
                eccentricity,
                equivalent_diameter,
                euler_number,
                extent,
                filled_area,
                major_axis_length,
                minor_axis_length,
                orientation,
                perimeter,
                solidity,
            ]

            # (rgb color space) [13 x 3 features]
            feats_r = self._color_features_per_channel(
                sp_rgb[:, :, 0], img_square[:, :, 0], mask_idx, mask_size
            )
            feats_g = self._color_features_per_channel(
                sp_rgb[:, :, 1], img_square[:, :, 1], mask_idx, mask_size
            )
            feats_b = self._color_features_per_channel(
                sp_rgb[:, :, 2], img_square[:, :, 2], mask_idx, mask_size
            )
            feats_color = [feats_r, feats_g, feats_b]
            feats_color = [item for sublist in feats_color for item in sublist]

            # Entropy (gray color space) [1 feature]
            entropy = cv2.mean(img_entropy, mask=sp_mask)[0]

            # GLCM texture features (gray color space) [5 features]
            glcm = greycomatrix(sp_gray, [1], [0])
            # Filter out the first row and column
            filt_glcm = glcm[1:, 1:, :, :]

            glcm_contrast = greycoprops(filt_glcm, prop="contrast")
            glcm_contrast = glcm_contrast[0, 0]
            glcm_dissimilarity = greycoprops(filt_glcm, prop="dissimilarity")
            glcm_dissimilarity = glcm_dissimilarity[0, 0]
            glcm_homogeneity = greycoprops(filt_glcm, prop="homogeneity")
            glcm_homogeneity = glcm_homogeneity[0, 0]
            glcm_energy = greycoprops(filt_glcm, prop="energy")
            glcm_energy = glcm_energy[0, 0]
            glcm_ASM = greycoprops(filt_glcm, prop="ASM")
            glcm_ASM = glcm_ASM[0, 0]

            feats_texture = [
                entropy,
                glcm_contrast,
                glcm_dissimilarity,
                glcm_homogeneity,
                glcm_energy,
                glcm_ASM,
            ]

            sp_feats = feats_shape + feats_color + feats_texture

            features = np.hstack(sp_feats)
            node_feat.append(features)

        node_feat = np.vstack(node_feat)
        return torch.Tensor(node_feat)


class InstanceMapPatchDataset(Dataset):
    """Helper class to use a give image and extracted instance maps as a dataset"""

    def __init__(
        self,
        image: np.ndarray,
        instance_map: np.ndarray,
        size: int,
        fill_value: Optional[int],
        mean: Optional[List[float]] = None,
        std: Optional[List[float]] = None,
    ) -> None:
        """Create a dataset for a given image and extracted instance maps with desired patches
           of (size, size, 3). If fill_value is not None, it fills up pixels outside the
           instance maps with this value (all channels)

        Args:
            image (np.ndarray): RGB input image
            instance maps (np.ndarray): Extracted instance maps
            size (int): Desired size of patches
            fill_value (Optional[None]): Value to fill outside the instance maps
                                         (None means do not fill)
        """
        self.image = image
        self.instance_map = instance_map
        self.properties = regionprops(instance_map)
        basic_transforms = [
            transforms.Resize(224),
            transforms.ToTensor(),
        ]
        if mean is not None and std is not None:
            basic_transforms.append(transforms.Normalize(mean, std))
        self.dataset_transform = transforms.Compose(basic_transforms)
        self.patch_size = (size, size, 3)
        self.fill_value = fill_value

    def _get_instance_patch(self, region_property: _RegionProperties) -> np.ndarray:
        """Returns the image patch with the correct padding for a given region property

        Args:
            region_property (_RegionProperties): Region property of the instance maps

        Returns:
            np.ndarray: Representative image patch
        """
        # Prepare input and output data
        output_image = np.ones(self.patch_size, dtype=np.uint8)
        if self.fill_value is not None:
            output_image *= self.fill_value
        else:
            output_image *= 255  # Have a white background in case we are at the border

        # Extract center
        center_x, center_y = region_property.centroid
        center_x = int(round(center_x))
        center_y = int(round(center_y))

        # Extract only super pixel
        if self.fill_value is not None:
            min_x, min_y, max_x, max_y = region_property.bbox
            x_length = max_x - min_x
            y_length = max_y - min_y

        # Handle no mask scenario and too large instance maps
        if self.fill_value is None or x_length > self.patch_size[0]:
            min_x = center_x - (self.patch_size[0] // 2)
            max_x = center_x + (self.patch_size[0] // 2)

        if self.fill_value is None or y_length > self.patch_size[1]:
            min_y = center_y - (self.patch_size[1] // 2)
            max_y = center_y + (self.patch_size[1] // 2)

        # Handle border cases
        min_x = max(0, min_x)
        min_y = max(0, min_y)
        max_x = min(self.image.shape[0], max_x)
        max_y = min(self.image.shape[1], max_y)
        x_length = max_x - min_x
        y_length = max_y - min_y
        assert x_length <= self.patch_size[0]
        assert y_length <= self.patch_size[1]

        # Actual image copying
        image_top_left = (
            ((self.patch_size[0] - x_length) // 2),
            ((self.patch_size[1] - y_length) // 2),
        )
        image_region = self.image[min_x:max_x, min_y:max_y]
        mask_region = (self.instance_map != region_property.label)[
            min_x:max_x, min_y:max_y
        ]
        if self.fill_value is not None:
            image_region[mask_region] = self.fill_value
        output_image[
            image_top_left[0] : image_top_left[0] + x_length,
            image_top_left[1] : image_top_left[1] + y_length,
        ] = image_region
        return output_image

    def __getitem__(self, index: int) -> Tuple[int, torch.Tensor]:
        """Loads an image for a given instance maps index

        Args:
            index (int): Instance index

        Returns:
            Tuple[int, torch.Tensor]: instance_index, image as tensor
        """
        input_image = self._get_instance_patch(self.properties[index])
        transformed_image = self.dataset_transform(Image.fromarray(input_image))
        return index, transformed_image

    def __len__(self) -> int:
        """Returns the length of the dataset

        Returns:
            int: Length of the dataset
        """
        return len(self.properties)


class PatchFeatureExtractor:
    """Helper class to use a CNN to extract features from an image"""

    def __init__(self, architecture: str, device: torch.device) -> None:
        """Create a patch feature extracter of a given architecture and put it on GPU if available

        Args:
            architecture (str): String of architecture. According to torchvision.models syntax
        """
        self.device = device

        if architecture.startswith("s3://mlflow"):
            model = self._get_mlflow_model(url=architecture)
        elif architecture.endswith(".pth"):
            model = self._get_local_model(path=architecture)
        else:
            model = self._get_torchvision_model(architecture).to(self.device)

        self.num_features = self._get_num_features(model)
        self.model = self._remove_classifier(model)
        self.model.eval()

    @staticmethod
    def _get_num_features(model: nn.Module) -> int:
        """Get the number of features of a given model

        Args:
            model (nn.Module): A PyTorch model

        Returns:
            int: The number of features it has
        """
        if hasattr(model, "model"):
            model = model.model
        if isinstance(model, torchvision.models.resnet.ResNet):
            return model.fc.in_features
        else:
            classifier = model.classifier[-1]
            if isinstance(classifier, nn.Sequential):
                classifier = classifier[-1]
            return classifier.in_features

    def _get_local_model(self, path: str) -> nn.Module:
        """Load a model from a local path

        Args:
            path (str): Path to the model

        Returns:
            nn.Module: A PyTorch model
        """
        model = torch.load(path, map_location=self.device)
        return model

    def _get_mlflow_model(self, url: str) -> nn.Module:
        """Load a MLflow model from a given URL

        Args:
            url (str): Model url

        Returns:
            nn.Module: A PyTorch model
        """
        import mlflow

        model = mlflow.pytorch.load_model(url, map_location=self.device)
        return model

    def _get_torchvision_model(self, architecture: str) -> nn.Module:
        """Returns a torchvision model from a given architecture string

        Args:
            architecture (str): Torchvision model description

        Returns:
            nn.Module: A pretrained pytorch model
        """
        model_class = dynamic_import_from("torchvision.models", architecture)
        model = model_class(pretrained=True)
        model = model.to(self.device)
        return model

    @staticmethod
    def _remove_classifier(model: nn.Module) -> nn.Module:
        """Returns the model without the classifier to get embeddings

        Args:
            model (nn.Module): Classifiation model

        Returns:
            nn.Module: Embedding model
        """
        if hasattr(model, "model"):
            model = model.model
        if isinstance(model, torchvision.models.resnet.ResNet):
            model.fc = nn.Sequential()
        else:
            model.classifier[-1] = nn.Sequential()
        return model

    def __call__(self, patch: torch.Tensor) -> torch.Tensor:
        """Computes the embedding of a normalized image input

        Args:
            image (torch.Tensor): Normalized image input

        Returns:
            torch.Tensor: Embedding of image
        """
        patch = patch.to(self.device)
        with torch.no_grad():
            embeddings = self.model(patch).squeeze()
            return embeddings


class DeepFeatureExtractor(FeatureExtractor):
    """Helper class to extract deep features from instance maps"""

    def __init__(
        self,
        architecture: str,
        mask: bool = False,
        size: int = 224,
        normalizer: Optional[dict] = None,
        batch_size: int = 32,
        num_workers: int = 0,
        **kwargs,
    ) -> None:
        """Create a deep feature extractor

        Args:
            architecture (str): Name of the architecture to use. According to torchvision.models syntax
            mask (bool, optional): Whether to mask out the parts outside the instance maps. Defaults to True.
            size (int, optional): Desired size of patches. Defaults to 224.
        """
        self.architecture = self._preprocess_architecture(architecture)
        self.mask = mask
        self.size = size
        if normalizer is not None:
            self.normalizer = normalizer.get("type", "unknown")
        else:
            self.normalizer = None
        super().__init__(**kwargs)

        # Handle GPU
        cuda = torch.cuda.is_available()
        self.device = torch.device("cuda:0" if cuda else "cpu")

        if normalizer is not None:
            self.normalizer_mean = normalizer.get("mean", [0, 0, 0])
            self.normalizer_std = normalizer.get("std", [1, 1, 1])
        else:
            self.normalizer_mean = None
            self.normalizer_std = None
        self.patch_feature_extractor = PatchFeatureExtractor(
            architecture, device=self.device
        )
        self.fill_value = 255 if self.mask else None
        self.batch_size = batch_size
        self.architecture_unprocessed = architecture
        self.num_workers = num_workers
        if self.num_workers in [0, 1]:
            torch.set_num_threads(1)

    @staticmethod
    def _preprocess_architecture(architecture: str) -> str:
        """Preprocess the architecture string to avoid characters that are not allowed as paths

        Args:
            architecture (str): Unprocessed architecture name

        Returns:
            str: Architecture name to use for the save path
        """
        if architecture.startswith("s3://mlflow"):
            _, experiment_id, run_id, _, metric = architecture[5:].split("/")
            return f"MLflow({experiment_id},{run_id},{metric})"
        elif architecture.endswith(".pth"):
            return f"Local({architecture.replace('/', '_')})"
        else:
            return architecture

    def _extract_features(
        self, input_image: np.ndarray, instance_map: np.ndarray
    ) -> torch.Tensor:
        """Extract features for a given RGB image and its extracted instance_map

        Args:
            input_image (np.ndarray): RGB input image
            instance_map (np.ndarray): Extracted instance_map

        Returns:
            torch.Tensor: Extracted features of shape [nr_instances, nr_features]
        """
        image_dataset = InstanceMapPatchDataset(
            input_image,
            instance_map,
            self.size,
            self.fill_value,
            self.normalizer_mean,
            self.normalizer_std,
        )
        image_loader = DataLoader(
            image_dataset,
            shuffle=False,
            batch_size=self.batch_size,
            num_workers=self.num_workers,
        )
        features = torch.empty(
            size=(len(image_dataset), self.patch_feature_extractor.num_features),
            dtype=torch.float32,
            device=self.device,
        )
        for i, image_batch in image_loader:
            embeddings = self.patch_feature_extractor(image_batch)
            features[i, :] = embeddings
        return features.cpu().detach()


class AugmentedInstanceMapPatchDataset(InstanceMapPatchDataset):
    """Helper class to use a give image and extracted instance maps as a dataset and provides the ability to change the dataset transform at run time"""

    def __init__(
        self,
        image: np.ndarray,
        instance_map: np.ndarray,
        size: int,
        fill_value: Optional[int],
        mean: Optional[List[float]],
        std: Optional[List[float]],
    ) -> None:
        super().__init__(image, instance_map, size, fill_value)
        self.mean = mean
        self.std = std

    def set_augmentation(self, augmentor):
        basic_transforms = [transforms.Resize(224)]
        basic_transforms.append(augmentor)
        basic_transforms.append(transforms.ToTensor())
        if self.mean is not None and self.std is not None:
            basic_transforms.append(transforms.Normalize(self.mean, self.std))
        self.dataset_transform = transforms.Compose(basic_transforms)


class AugmentedDeepFeatureExtractor(DeepFeatureExtractor):
    """Helper class to extract deep features from instance maps with different augmentations"""

    def __init__(
        self,
        rotations: Optional[List[int]] = None,
        flips: Optional[List[int]] = None,
        **kwargs,
    ) -> None:
        """Creates a feature extractor that extracts feature for all of the given augmentations. Otherwise works the same as the DeepFeatureExtractor

        Args:
            rotations (Optional[List[int]], optional): List of rotations to use. Defaults to None.
            flips (Optional[List[int]], optional): List of flips to use, in {'n', 'h', 'v'}. Defaults to None.
        """
        self.rotations = rotations
        self.flips = flips
        super().__init__(**kwargs)
        if rotations is None:
            rotations = [0]
        if flips is None:
            flips = ["n"]
        self.transforms = []
        for angle in rotations:
            for flip in flips:
                t = [
                    transforms.Lambda(
                        lambda x, a=angle: transforms.functional.rotate(x, angle=a)
                    )
                ]
                if flip == "h":
                    t.append(
                        transforms.Lambda(lambda x: transforms.functional.hflip(x))
                    )
                if flip == "v":
                    t.append(
                        transforms.Lambda(lambda x: transforms.functional.vflip(x))
                    )
                self.transforms.append(transforms.Compose(t))

    def _extract_features(
        self, input_image: np.ndarray, instance_map: np.ndarray
    ) -> torch.Tensor:
        """Extract features for a given RGB image and its extracted instance_map for all augmentations

        Args:
            input_image (np.ndarray): RGB input image
            instance_map (np.ndarray): Extracted instance_map

        Returns:
            torch.Tensor: Extracted features of shape [nr_instances, nr_augmentations, nr_features]
        """
        image_dataset = AugmentedInstanceMapPatchDataset(
            input_image,
            instance_map,
            self.size,
            self.fill_value,
            self.normalizer_mean,
            self.normalizer_std,
        )
        image_loader = DataLoader(
            image_dataset,
            shuffle=False,
            batch_size=self.batch_size,
            num_workers=self.num_workers,
        )
        features = torch.empty(
            size=(
                len(image_dataset),
                len(self.transforms),
                self.patch_feature_extractor.num_features,
            ),
            dtype=torch.float32,
            device=self.device,
        )
        for i, transform in enumerate(self.transforms):
            image_dataset.set_augmentation(transform)
            for j, image_batch in image_loader:
                embeddings = self.patch_feature_extractor(image_batch)
                features[j, i, :] = embeddings
        return features.cpu().detach()


class FeatureMerger(PipelineStep):
    def __init__(self, downsampling_factor: int, *args, **kwargs) -> None:
        """Merge features from an initial instance map to a merged instance map by averaging the features."""
        warnings.warn(
            "FeatureMerger is depreciated. Use AverageFeatureMerger instead.",
            DeprecationWarning,
        )
        self.downsampling_factor = downsampling_factor
        super().__init__(*args, **kwargs)

    @staticmethod
    def _downsample(image: np.ndarray, downsampling_factor: int) -> np.ndarray:
        """Downsample an input image with a given downsampling factor

        Args:
            image (np.array): Input tensor
            downsampling_factor (int): Factor to downsample

        Returns:
            np.array: Output tensor
        """
        height, width = image.shape[0], image.shape[1]
        new_height = math.floor(height / downsampling_factor)
        new_width = math.floor(width / downsampling_factor)
        downsampled_image = cv2.resize(
            image, (new_height, new_width), interpolation=cv2.INTER_NEAREST
        )
        return downsampled_image

    def process(
        self,
        instance_map: np.ndarray,
        merged_instance_map: np.ndarray,
        features: torch.Tensor,
    ) -> torch.Tensor:
        """Merge features from an initial instance_map to a merged_instance_map by feature averaging

        Args:
            instance_map (np.ndarray): Initial instance map
            merged_instance_map (np.ndarray): Merged instance map that overlaps with initial instance_map
            features (torch.Tensor): Extracted features

        Raises:
            NotImplementedError: Only 1D and 2D features supported

        Returns:
            torch.Tensor: Merged features
        """
        if self.downsampling_factor != 1:
            instance_map = self._downsample(instance_map, self.downsampling_factor)
            merged_instance_map = self._downsample(
                merged_instance_map, self.downsampling_factor
            )
        translator = self._get_translator(instance_map, merged_instance_map)
        if len(features.shape) == 2:
            return self._merge_features(features, translator)
        elif len(features.shape) == 3:
            return self._merge_augmented_features(features, translator)
        else:
            raise NotImplementedError

    @staticmethod
    def _check_translator_consistency(
        instance_map, merged_instance_map, translator
    ) -> None:
        for instance_id in np.unique(merged_instance_map):
            assert (
                instance_id in translator
            ), f"Merged instance id {instance_id} is not mapped to any superpixel: {translator}"
            assert (
                len(translator[instance_id]) > 0
            ), f"Merged instance id {instance_id} is not mapped to any superpixel: {translator}"
        all_values = np.concatenate(list(translator.values()))
        assert len(all_values) == len(
            set(all_values)
        ), f"Mapped values contain duplicates: {all_values}"
        all_values = set(all_values)
        for instance_id in np.unique(instance_map):
            assert (
                instance_id in all_values
            ), f"Inital instance id {instance_id} does not appear in translator"

    def _get_translator(
        self, instance_map: np.ndarray, merged_instance_map: np.ndarray
    ) -> Dict[int, int]:
        """Calculate which instances of the initial instance map belong to each instance of the merged instance map

        Args:
            instance_map (np.ndarray): Initial instance map
            merged_instance_map (np.ndarray): Merged instance map

        Returns:
            Dict[int, int]: Mapping from merged instance map id to initial instance map id
        """
        nr_spx = instance_map.max() + 1
        translator = defaultdict(list)
        for i in range(1, nr_spx):
            mask = instance_map == i
            assignments, counts = np.unique(
                merged_instance_map[mask], return_counts=True
            )
            assignment = assignments[counts.argmax()]
            translator[assignment].append(i)
        self._check_translator_consistency(
            instance_map, merged_instance_map, translator
        )
        return {k: np.array(v) for k, v in translator.items()}

    @staticmethod
    def _merge_features(
        features: torch.Tensor, translator: Dict[int, int]
    ) -> torch.Tensor:
        """Merge regular one-dimensional features

        Args:
            features (torch.Tensor): Feature matrix of shape (nr_superpixels, latent_dimension)
            translator (Dict[int, int]): Mapping from original superpixel index to merged superpixel index

        Returns:
            torch.Tensor: Merged features of shape (nr_merged_superpixels, latent_dimension)
        """
        latent_dim = features.shape[1]
        merged_features = np.empty((len(translator), latent_dim))
        for index, values in translator.items():
            merged_features[index - 1] = features[values - 1].mean(axis=0)
        return torch.as_tensor(merged_features)

    @staticmethod
    def _merge_augmented_features(
        features: torch.Tensor, translator: Dict[int, int]
    ) -> torch.Tensor:
        """Merge augmented one-dimensional features

        Args:
            features (torch.Tensor): Feature matrix of shape (nr_superpixels, nr_augmentations, latent_dimension)
            translator (Dict[int, int]): Mapping from original superpixel index to merged superpixel index

        Returns:
            torch.Tensor: Merged features of shape (nr_merged_superpixels, nr_augmentations, latent_dimension)
        """
        nr_augmentations = features.shape[1]
        latent_dim = features.shape[2]
        merged_features = np.empty((len(translator), nr_augmentations, latent_dim))
        for index, values in translator.items():
            merged_features[index - 1] = features[values - 1].mean(axis=0)
        return torch.as_tensor(merged_features)


class AverageFeatureMerger(PipelineStep):
    def __init__(self, *args, **kwargs) -> None:
        """Merge features from an initial instance map to a merged instance map by averaging the features."""
        super().__init__(*args, **kwargs)

    def process(self, features, translator):
        if len(features.shape) == 2:
            return self._merge_features(features, translator)
        elif len(features.shape) == 3:
            return self._merge_augmented_features(features, translator)
        else:
            raise NotImplementedError

    @staticmethod
    def _merge_features(
        features: torch.Tensor, translator: Dict[int, int]
    ) -> torch.Tensor:
        """Merge regular one-dimensional features

        Args:
            features (torch.Tensor): Feature matrix of shape (nr_superpixels, latent_dimension)
            translator (Dict[int, int]): Mapping from original superpixel index to merged superpixel index

        Returns:
            torch.Tensor: Merged features of shape (nr_merged_superpixels, latent_dimension)
        """
        latent_dim = features.shape[1]
        merged_features = np.empty((len(translator), latent_dim))
        for index, values in translator.items():
            merged_features[index - 1] = features[values - 1].mean(axis=0)
        return torch.as_tensor(merged_features)

    @staticmethod
    def _merge_augmented_features(
        features: torch.Tensor, translator: Dict[int, int]
    ) -> torch.Tensor:
        """Merge augmented one-dimensional features

        Args:
            features (torch.Tensor): Feature matrix of shape (nr_superpixels, nr_augmentations, latent_dimension)
            translator (Dict[int, int]): Mapping from original superpixel index to merged superpixel index

        Returns:
            torch.Tensor: Merged features of shape (nr_merged_superpixels, nr_augmentations, latent_dimension)
        """
        nr_augmentations = features.shape[1]
        latent_dim = features.shape[2]
        merged_features = np.empty((len(translator), nr_augmentations, latent_dim))
        for index, values in translator.items():
            merged_features[index - 1] = features[values - 1].mean(axis=0)
        return torch.as_tensor(merged_features)
