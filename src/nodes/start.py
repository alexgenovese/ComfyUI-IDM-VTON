import sys, os
sys.path.append('.')
sys.path.append('..')

dir_path = os.path.dirname(os.path.realpath(__file__))
print(f"VTO | Current path {dir_path}")

from PIL import Image
from ..src.tryon_pipeline import StableDiffusionXLInpaintPipeline as TryonPipeline
from ..src.unet_hacked_garmnet import UNet2DConditionModel as UNet2DConditionModel_ref
from ..src.unet_hacked_tryon import UNet2DConditionModel
from transformers import (
    CLIPImageProcessor,
    CLIPVisionModelWithProjection,
    CLIPTextModel,
    CLIPTextModelWithProjection,
)
from diffusers import DDPMScheduler,AutoencoderKL
from typing import List

import torch
from transformers import AutoTokenizer
import numpy as np
from .utils_mask import get_mask_location
from torchvision import transforms
from torchvision.transforms.functional import to_pil_image
from comfy.model_management import get_torch_device
import folder_paths
from comfy.utils import ProgressBar


DEVICE = get_torch_device()
MAX_RESOLUTION = 16384
device = DEVICE
pbar = None
base_path = os.path.join(folder_paths.models_dir,"checkpoints", "IDM-VTON")
IDM_WEIGHTS_PATH = os.path.join(folder_paths.models_dir,"checkpoints", "IDM-VTON")


def pil_to_binary_mask(pil_image, threshold=0):
    np_image = np.array(pil_image)
    grayscale_image = Image.fromarray(np_image).convert("L")
    binary_mask = np.array(grayscale_image) > threshold
    mask = np.zeros(binary_mask.shape, dtype=np.uint8)
    for i in range(binary_mask.shape[0]):
        for j in range(binary_mask.shape[1]):
            if binary_mask[i,j] == True :
                mask[i,j] = 1
    mask = (mask*255).astype(np.uint8)
    output_mask = Image.fromarray(mask)
    return output_mask



class IDM_VTON:
    def __init__(self) -> None:
        self.pbar = None 

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "human_img": ("IMAGE",),
                "pose_img": ("IMAGE",),
                "garm_img": ("IMAGE",),
                "garment_des": ("STRING", {"multiline": True, "dynamicPrompts": True}),
                "negative_prompt": ("STRING", {"multiline": True, "dynamicPrompts": True, "default" : "monochrome, lowres, bad anatomy, worst quality, low quality"}),
                "cloth_position": ("STRING", ["upper_body", "lower_body", "dresses"], ),
                "width": ("INT", {"default": 768, "min": 0, "max": MAX_RESOLUTION}),
                "height": ("INT", {"default": 1024, "min": 0, "max": MAX_RESOLUTION}),
                "denoise_steps": ("INT", {"default": 30 }),
                "seed": ("INT", {"default": 42, "min": 0, "max": 0xffffffffffffffff}),
            }
        }
    

    RETURN_TYPES = ("IMAGE", )
    FUNCTION = "start_tryon"
    CATEGORY = "ComfyUI-IDM-VTON"

    def start_tryon( self, human_img, garm_img, cloth_position, pose_img, height, width, garment_des, negative_prompt, denoise_steps, seed):
        print(f'VTO | Start Try On - Steps: {denoise_steps}')
        human_img, garm_img, pose_img = self.preprocess_images(human_img, garm_img, pose_img, height, width)

        print('VTO | Preprocessed images completed')

        unet = UNet2DConditionModel.from_pretrained(
            base_path,
            subfolder="unet",
            torch_dtype=torch.float16,
            cache_dir = IDM_WEIGHTS_PATH
        )
        unet.requires_grad_(False)
        tokenizer_one = AutoTokenizer.from_pretrained(
            base_path,
            subfolder="tokenizer",
            revision=None,
            use_fast=False,
            cache_dir = IDM_WEIGHTS_PATH
        )
        tokenizer_two = AutoTokenizer.from_pretrained(
            base_path,
            subfolder="tokenizer_2",
            revision=None,
            use_fast=False,
            cache_dir = IDM_WEIGHTS_PATH
        )
        noise_scheduler = DDPMScheduler.from_pretrained(base_path, subfolder="scheduler", cache_dir = IDM_WEIGHTS_PATH)

        text_encoder_one = CLIPTextModel.from_pretrained(
            base_path,
            subfolder="text_encoder",
            torch_dtype=torch.float16,
            cache_dir = IDM_WEIGHTS_PATH
        )
        text_encoder_two = CLIPTextModelWithProjection.from_pretrained(
            base_path,
            subfolder="text_encoder_2",
            torch_dtype=torch.float16,
            cache_dir = IDM_WEIGHTS_PATH
        )
        image_encoder = CLIPVisionModelWithProjection.from_pretrained(
            base_path,
            subfolder="image_encoder",
            torch_dtype=torch.float16,
            cache_dir = IDM_WEIGHTS_PATH
            )
        vae = AutoencoderKL.from_pretrained(base_path,
                                            subfolder="vae",
                                            torch_dtype=torch.float16,
                                            cache_dir = IDM_WEIGHTS_PATH
        )

        print('VTO | Diffusion Weights Loaded ')

        # "stabilityai/stable-diffusion-xl-base-1.0",
        UNet_Encoder = UNet2DConditionModel_ref.from_pretrained(
            base_path,
            subfolder="unet_encoder",
            torch_dtype=torch.float16,
            cache_dir = IDM_WEIGHTS_PATH
        )

        UNet_Encoder.requires_grad_(False)
        image_encoder.requires_grad_(False)
        vae.requires_grad_(False)
        unet.requires_grad_(False)
        text_encoder_one.requires_grad_(False)
        text_encoder_two.requires_grad_(False)
        tensor_transfrom = transforms.Compose(
                    [
                        transforms.ToTensor(),
                        transforms.Normalize([0.5], [0.5]),
                    ]
            )

        print('VTO | Unet setup completed')

        pipe = TryonPipeline.from_pretrained(
                base_path,
                unet=unet,
                vae=vae,
                feature_extractor= CLIPImageProcessor(),
                text_encoder = text_encoder_one,
                text_encoder_2 = text_encoder_two,
                tokenizer = tokenizer_one,
                tokenizer_2 = tokenizer_two,
                scheduler = noise_scheduler,
                image_encoder=image_encoder,
                torch_dtype=torch.float16,
        )
        pipe.unet_encoder = UNet_Encoder

        print('VTO | TryonPipeline setup completed')

        pipe.to(DEVICE)
        pipe.unet_encoder.to(DEVICE)

        print('VTO | auto_crop_and_resizing')
        
        # --- Start auto_crop_and_resizing Yes
        width, height = human_img.size
        target_width = int(min(width, height * (3 / 4)))
        target_height = int(min(height, width * (4 / 3)))
        left = (width - target_width) / 2
        top = (height - target_height) / 2
        right = (width + target_width) / 2
        bottom = (height + target_height) / 2
        cropped_img = human_img.crop((left, top, right, bottom))
        crop_size = cropped_img.size
        human_img = cropped_img.resize((width,height))
        # --- END auto_crop_and_resizing Yes
        
        print('VTO | auto_generated_mask')

        # --- Start auto_generated_mask Yes
        from ..preprocess.openpose.run_openpose import OpenPose
        openpose_model = OpenPose(0)
        keypoints = openpose_model( human_img.resize((384,512)) )

        from ..preprocess.humanparsing.run_parsing import Parsing
        parsing_model = Parsing(0)
        model_parse, _ = parsing_model(human_img.resize((384,512)))

        mask, mask_gray = get_mask_location('hd', cloth_position, model_parse, keypoints)
        mask = mask.resize((width,height))

        mask_gray = (1-transforms.ToTensor()(mask)) * tensor_transfrom(human_img)
        mask_gray = to_pil_image((mask_gray+1.0)/2.0)
        # --- END auto_generated_mask Yes

        print('VTO | Created Mask Step 2 completed')

        with torch.no_grad():
            # Extract the images
            with torch.cuda.amp.autocast():
                with torch.no_grad():
                    prompt = garment_des
                    # negative_prompt = "monochrome, lowres, bad anatomy, worst quality, low quality"
                    with torch.inference_mode():
                        (
                            prompt_embeds,
                            negative_prompt_embeds,
                            pooled_prompt_embeds,
                            negative_pooled_prompt_embeds,
                        ) = pipe.encode_prompt(
                            prompt,
                            num_images_per_prompt=1,
                            do_classifier_free_guidance=True,
                            negative_prompt=negative_prompt,
                        )
                                        
                        # negative_prompt = "monochrome, lowres, bad anatomy, worst quality, low quality"
                        if not isinstance(prompt, List):
                            prompt = [prompt] * 1
                        if not isinstance(negative_prompt, List):
                            negative_prompt = [negative_prompt] * 1
                        with torch.inference_mode():
                            (
                                prompt_embeds_c,
                                _,
                                _,
                                _,
                            ) = pipe.encode_prompt(
                                prompt,
                                num_images_per_prompt=1,
                                do_classifier_free_guidance=False,
                                negative_prompt=negative_prompt,
                            )
                        
                        pose_img = tensor_transfrom(pose_img).unsqueeze(0).to(device, torch.float16)
                        garm_tensor = tensor_transfrom(garm_img).unsqueeze(0).to(device,torch.float16)

                        print('VTO | Start generating...')
                        self.pbar = ProgressBar(denoise_steps)

                        generator = torch.Generator(device).manual_seed(seed) if seed is not None else None
                        images = pipe(
                            prompt_embeds=prompt_embeds.to(device,torch.float16),
                            negative_prompt_embeds=negative_prompt_embeds.to(device,torch.float16),
                            pooled_prompt_embeds=pooled_prompt_embeds.to(device,torch.float16),
                            negative_pooled_prompt_embeds=negative_pooled_prompt_embeds.to(device,torch.float16),
                            num_inference_steps=denoise_steps,
                            generator=generator,
                            strength = 1.0,
                            pose_img = pose_img,
                            text_embeds_cloth=prompt_embeds_c.to(device,torch.float16),
                            cloth = garm_tensor,
                            mask_image=mask,
                            image=human_img, 
                            height=height,
                            width=width,
                            ip_adapter_image = garm_img,
                            guidance_scale=2.0,
                            callback_on_step_end=self.callback_update_progressbar,
                            callback_on_step_end_tensor_inputs=['prompt_embeds']
                        )[0]

                        print('VTO | End ')

                        images = [transforms.ToTensor()(image) for image in images]
                        images = [image.permute(1,2,0) for image in images]
                        images = torch.stack(images)

                        return (images, )
                        
    
    def preprocess_images(self, human_img, garment_img, pose_img, height, width):
        human_img = human_img.squeeze().permute(2,0,1)
        garment_img = garment_img.squeeze().permute(2,0,1)
        pose_img = pose_img.squeeze().permute(2,0,1)
        
        human_img = transforms.functional.to_pil_image(human_img)  
        garment_img = transforms.functional.to_pil_image(garment_img) 
        pose_img = transforms.functional.to_pil_image(pose_img)  
        
        human_img = human_img.convert("RGB").resize((width, height))
        garment_img = garment_img.convert("RGB").resize((width, height))
        pose_img = pose_img.convert("RGB").resize((width, height))
        
        return human_img, garment_img, pose_img

    def callback_update_progressbar(self, pipe, step_index, timestep, callback_kwargs):
        # https://huggingface.co/docs/diffusers/using-diffusers/callback
        # print("Callback callback_update_progressbar")
        self.pbar.update(1)

        # adjust the batch_size of prompt_embeds according to guidance_scale
        # if step_index == int(pipe.num_timesteps * 0.4):
        #         print("Callback adjust the batch_size")
        #         prompt_embeds = callback_kwargs["prompt_embeds"]
        #         prompt_embeds = prompt_embeds.chunk(2)[-1]

        #         # update guidance_scale and prompt_embeds
        #         pipe._guidance_scale = 0.0
        #         callback_kwargs["prompt_embeds"] = prompt_embeds
                
        return callback_kwargs
