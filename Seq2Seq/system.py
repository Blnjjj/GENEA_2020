from pathlib import Path
from collections import OrderedDict
from argparse import Namespace

import pytorch_lightning as pl
import torch
from torch.nn import MSELoss
from torch.nn import functional as F
from torch.utils.data import DataLoader

from model import Encoder, Decoder, Discriminator
from dataset import Seq2SeqDataset


class Seq2SeqSystem(pl.LightningModule):
    def __init__(
        self,
        train_folder: str = "data/dataset/train",
        test_folder: str = "data/dataset/test",
        predicted_poses: int = 20,
        previous_poses: int = 10,
    ):
        super().__init__()
        self.encoder = Encoder(26, 150, 1)
        self.decoder = Decoder(45, 150, 300, max_gen=predicted_poses)
        self.predicted_poses = predicted_poses
        self.previous_poses = previous_poses
        self.loss = MSELoss()
        self.train_folder = train_folder
        self.test_folder = test_folder

    def forward(self, x, p):
        output, hidden = self.encoder(x)
        predicted_poses = self.decoder(output, hidden, p)
        return predicted_poses

    def calculate_loss(self, p, y):
        mse_loss = self.loss(p, y)
        cont_loss = torch.norm(p[1:] - p[:-1]) / (p.size(0) - 1)
        loss = mse_loss + cont_loss * 0.01
        return loss

    def training_step(self, batch, batch_nb):
        x, y, p = batch
        pred_poses = self.forward(x, p)
        loss = self.calculate_loss(pred_poses, y)
        return {"loss": loss}

    def validation_step(self, batch, batch_nb):
        x, y, p = batch
        pred_poses = self.forward(x, p)
        loss = self.calculate_loss(pred_poses, y)
        return {"loss": loss}

    def validation_epoch_end(self, outputs):
        d = {"val_loss": 0}
        for out in outputs:
            d["val_loss"] += out["loss"]
        return d

    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=0.001)

    def train_dataloader(self):
        dataset = Seq2SeqDataset(
            Path(self.train_folder).glob("*.npz"), self.previous_poses, self.predicted_poses
        )
        loader = DataLoader(
            dataset, batch_size=50, shuffle=True, collate_fn=dataset.collate_fn
        )
        return loader

    def val_dataloader(self):
        dataset = Seq2SeqDataset(
            Path(self.test_folder).glob("*.npz"),
            self.previous_poses,
            self.predicted_poses,
        )
        loader = DataLoader(
            dataset, batch_size=50, shuffle=False, collate_fn=dataset.collate_fn
        )
        return loader


class AdversarialSeq2SeqSystem(pl.LightningModule):

    def __init__(
            self,
            train_folder: str = "data/dataset/train",
            test_folder: str = "data/dataset/test",
            predicted_poses: int = 20,
            previous_poses: int = 10
    ):
        super().__init__()
        self.hparams = Namespace(**{
            'prev_poses': previous_poses,
            'pred_poses': predicted_poses,
            'lr': 1e-3,
            'beta1': 0.5,
            'beta2': 0.9,
            'w_cont_loss': 0.01,
            'w_adv_loss': 0.01
        })
        self.encoder = Encoder(26, 150, 1)
        self.decoder = Decoder(45, 150, 300, max_gen=predicted_poses)
        self.discriminator = Discriminator(45, ch_hid=100)
        self.predicted_poses = predicted_poses
        self.previous_poses = previous_poses
        self.base_loss = MSELoss()
        self.train_folder = train_folder
        self.test_folder = test_folder

    def forward(self, x, p):
        output, hidden = self.encoder(x)
        predicted_poses = self.decoder(output, hidden, p)
        return predicted_poses

    def calculate_loss(self, p, y):
        base_loss = self.base_loss(p, y)
        cont_loss = torch.norm(p[1:] - p[:-1]) / (p.size(0) - 1)
        loss = base_loss + cont_loss * self.hparams.w_cont_loss
        return loss

    def training_step(self, batch, batch_nb, optimizer_idx):
        audio_features, real_poses, prev_poses = batch
        pred_poses = self.forward(audio_features, prev_poses)
        loss = self.calculate_loss(pred_poses, real_poses)
        # return {"loss": loss}

        # train generator
        if optimizer_idx == 0:
            base_loss = self.base_loss(pred_poses, real_poses)
            cont_loss = torch.norm(pred_poses[1:] - pred_poses[:-1]) / (pred_poses.size(0) - 1)

            is_real = torch.ones((pred_poses.size(0), 1)).to(pred_poses.device)
            adv_loss = F.binary_cross_entropy_with_logits(self.discriminator(pred_poses), is_real)

            loss = (base_loss + self.hparams.w_cont_loss * cont_loss
                    + self.hparams.w_adv_loss * adv_loss)
            # aux metrics
            d_fake_score = F.sigmoid(self.discriminator(pred_poses)).mean()
            logs = {
                'loss': loss,
                # loss components
                'g_adv_loss': adv_loss,
                'base_loss': base_loss,
                'cont_loss': cont_loss,
                # metrics
                'd_fake_score': d_fake_score
            }

            return OrderedDict({
                'loss': loss,
                'progress_bar': {'total_loss': loss, 'd_fake_score': d_fake_score},
                'log': logs
            })

            # adversarial loss is binary cross-entropy
            # g_loss = self.adversarial_loss(self.discriminator(self.generated_imgs), valid)
            # tqdm_dict = {'g_loss': g_loss}
            # output = OrderedDict({
            #     'loss': g_loss,
            #     'progress_bar': tqdm_dict,
            #     'log': tqdm_dict
            # })
            # return output

        # train discriminator
        if optimizer_idx == 1:
            d_real_scores = self.discriminator(real_poses)
            d_fake_scores = self.discriminator(pred_poses.detach())

            is_real = torch.ones((pred_poses.size(0), 1)).to(pred_poses.device)
            real_loss = F.binary_cross_entropy_with_logits(d_real_scores, is_real)
            fake_loss = F.binary_cross_entropy_with_logits(d_fake_scores, 1 - is_real)

            d_loss = (real_loss + fake_loss) / 2
            # aux metrics
            d_fake_scores = F.sigmoid(d_fake_scores).mean()
            d_real_scores = F.sigmoid(d_real_scores).mean()

            logs = {
                'loss': d_loss,
                'd_real_loss': real_loss,
                'd_fake_loss': fake_loss,
                'd_fake_score': d_fake_scores,
                'd_real_score': d_real_scores
            }
            return OrderedDict({
                'loss': d_loss,
                'progress_bar': {'d_loss': d_loss, 'd_fake_score': d_fake_scores, 'd_real_score': d_real_scores},
                'log': logs
            })

            # # discriminator loss is the average of these
            # d_loss = (real_loss + fake_loss) / 2
            # tqdm_dict = {'d_loss': d_loss}
            # output = OrderedDict({
            #     'loss': d_loss,
            #     'progress_bar': tqdm_dict,
            #     'log': tqdm_dict
            # })

    def validation_step(self, batch, batch_nb):
        x, y, p = batch
        pred_poses = self.forward(x, p)
        loss = self.calculate_loss(pred_poses, y)
        return {"loss": loss}

    def validation_epoch_end(self, outputs):
        d = {"val_loss": 0}
        for out in outputs:
            d["val_loss"] += out["loss"]
        return d

    def configure_optimizers(self):
        lr = self.hparams.lr
        b1 = self.hparams.beta1
        b2 = self.hparams.beta2

        opt_g = torch.optim.Adam(list(self.encoder.parameters()) + list(self.decoder.parameters()), lr=lr, betas=(b1, b2))
        opt_d = torch.optim.Adam(self.discriminator.parameters(), lr=lr, betas=(b1, b2))
        return [opt_g, opt_d], []

    def train_dataloader(self):
        dataset = Seq2SeqDataset(
            Path(self.train_folder).glob("*.npz"), self.previous_poses, self.predicted_poses
        )
        loader = DataLoader(
            dataset, batch_size=50, shuffle=True, collate_fn=dataset.collate_fn
        )
        return loader

    def val_dataloader(self):
        dataset = Seq2SeqDataset(
            Path(self.test_folder).glob("*.npz"),
            self.previous_poses,
            self.predicted_poses,
        )
        loader = DataLoader(
            dataset, batch_size=50, shuffle=False, collate_fn=dataset.collate_fn
        )
        return loader
