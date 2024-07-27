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

DEVICE = get_torch_device()
MAX_RESOLUTION = 16384

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
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "human_img": ("IMAGE",),
                "openpose": ("IMAGE", ),
                "pose_img": ("IMAGE",),
                "mask_img": ("IMAGE",),
                "garm_img": ("IMAGE",),
                "garment_des": ("STRING", {"multiline": True, "dynamicPrompts": True}),
                "negative_prompt": ("STRING", {"multiline": True, "dynamicPrompts": True}),
                "width": ("INT", {"default": 768, "min": 0, "max": MAX_RESOLUTION}),
                "height": ("INT", {"default": 1024, "min": 0, "max": MAX_RESOLUTION}),
                "denoise_steps": ("INT", {"default": 30}),
                "is_checked_crop": ("BOOL", {"default": True}), # Use auto-generated mask (Takes 5 seconds)
                "is_checked": ("BOOL", {"default": True}), # Use auto-crop & resizing
                "seed": ("INT", {"default": 42, "min": 0, "max": 0xffffffffffffffff}),
            }
        }
    
    RETURN_TYPES = ("IMAGE", "MASK")
    FUNCTION = "start_tryon"
    CATEGORY = "ComfyUI-IDM-VTON"


    def start_tryon( self, human_img, openpose, garm_img, pose_img, mask_img, height, width, garment_des, negative_prompt, denoise_steps, is_checked_crop, is_checked, seed):
        device = DEVICE
        human_img, openpose, garm_img, pose_img, mask_img = self.preprocess_images(human_img, openpose, garm_img, pose_img, mask_img, height, width)

        base_path = 'yisol/IDM-VTON'

        unet = UNet2DConditionModel.from_pretrained(
            base_path,
            subfolder="unet",
            torch_dtype=torch.float16,
        )
        unet.requires_grad_(False)
        tokenizer_one = AutoTokenizer.from_pretrained(
            base_path,
            subfolder="tokenizer",
            revision=None,
            use_fast=False,
        )
        tokenizer_two = AutoTokenizer.from_pretrained(
            base_path,
            subfolder="tokenizer_2",
            revision=None,
            use_fast=False,
        )
        noise_scheduler = DDPMScheduler.from_pretrained(base_path, subfolder="scheduler")

        text_encoder_one = CLIPTextModel.from_pretrained(
            base_path,
            subfolder="text_encoder",
            torch_dtype=torch.float16,
        )
        text_encoder_two = CLIPTextModelWithProjection.from_pretrained(
            base_path,
            subfolder="text_encoder_2",
            torch_dtype=torch.float16,
        )
        image_encoder = CLIPVisionModelWithProjection.from_pretrained(
            base_path,
            subfolder="image_encoder",
            torch_dtype=torch.float16,
            )
        vae = AutoencoderKL.from_pretrained(base_path,
                                            subfolder="vae",
                                            torch_dtype=torch.float16,
        )

        # "stabilityai/stable-diffusion-xl-base-1.0",
        UNet_Encoder = UNet2DConditionModel_ref.from_pretrained(
            base_path,
            subfolder="unet_encoder",
            torch_dtype=torch.float16,
        )

        parsing_model = Parsing(0)

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

        pipe.to(DEVICE)
        pipe.unet_encoder.to(DEVICE)
        
        if is_checked_crop:
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
        else:
            human_img = human_img.resize((width,height))


        if is_checked:
            keypoints = openpose
            model_parse, _ = parsing_model(human_img.resize((384,512)))
            mask, mask_gray = get_mask_location('hd', "upper_body", model_parse, keypoints)
            mask = mask.resize((768,1024))
        else:
            mask = pil_to_binary_mask(dict['layers'][0].convert("RGB").resize((768, 1024)))
            # mask = transforms.ToTensor()(mask)
            # mask = mask.unsqueeze(0)
        mask_gray = (1-transforms.ToTensor()(mask)) * tensor_transfrom(human_img)
        mask_gray = to_pil_image((mask_gray+1.0)/2.0)

        
        with torch.no_grad():
            # Extract the images
            with torch.cuda.amp.autocast():
                with torch.no_grad():
                    prompt = "model is wearing " + garment_des
                    negative_prompt = "monochrome, lowres, bad anatomy, worst quality, low quality"
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
                                        
                        prompt = "a photo of " + garment_des
                        negative_prompt = "monochrome, lowres, bad anatomy, worst quality, low quality"
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
                        
                        pose_img =  tensor_transfrom(pose_img).unsqueeze(0).to(device, torch.float16)
                        garm_tensor =  tensor_transfrom(garm_img).unsqueeze(0).to(device,torch.float16)
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
                            ip_adapter_image = garm_img.resize((height,width)),
                            guidance_scale=2.0,
                        )[0]

                        images = [transforms.ToTensor()(image) for image in images]
                        images = [image.permute(1,2,0) for image in images]
                        images = torch.stack(images)

                        if is_checked_crop:
                            out_img = images[0].resize(crop_size)        
                            human_img.paste(out_img, (int(left), int(top)))    
                            # return human_img_orig, mask_gray
                            return (human_img, mask_gray, )
                        else:
                            # return images[0], mask_gray
                            return (images[0], mask_gray, )
    

    def preprocess_images(self, human_img, openpose, garment_img, pose_img, mask_img, height, width):
        human_img = human_img.squeeze().permute(2,0,1)
        garment_img = garment_img.squeeze().permute(2,0,1)
        openpose = openpose.squeeze().permute(2,0,1)
        pose_img = pose_img.squeeze().permute(2,0,1)
        mask_img = mask_img.squeeze().permute(2,0,1)
        
        human_img = transforms.functional.to_pil_image(human_img)  
        garment_img = transforms.functional.to_pil_image(garment_img)  
        openpose = transforms.functional.to_pil_image(openpose)  
        pose_img = transforms.functional.to_pil_image(pose_img)  
        mask_img = transforms.functional.to_pil_image(mask_img)
        
        human_img = human_img.convert("RGB").resize((width, height))
        garment_img = garment_img.convert("RGB").resize((width, height))
        openpose = openpose.convert("RGB").resize((width, height))
        mask_img = mask_img.convert("RGB").resize((width, height))
        pose_img = pose_img.convert("RGB").resize((width, height))
        
        return human_img, openpose, garment_img, pose_img, mask_img
    