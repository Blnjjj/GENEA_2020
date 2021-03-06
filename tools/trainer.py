import torch
import torch.nn as nn
from torch.utils.data import DataLoader
import math


class MotionTrainer:
    def __init__(self, train_iterator: DataLoader, test_iterator: DataLoader, model: nn.Module, model_name='best.pt',
                 criterion=nn.MSELoss()):
        self.model = model
        self.optimizer = torch.optim.Adam(model.parameters(), lr=0.0003, betas=(0.9, 0.999))
        self.train_iterator = train_iterator
        self.test_iterator = test_iterator
        self.criterion = criterion
        self.best_loss = math.inf
        self.model_name = model_name
        self.best_epoch = 0

    def train_epoch(self):
        total_loss = 0
        self.model.train()
        for batch_idx, (features, labels) in enumerate(self.train_iterator):
            self.optimizer.zero_grad()
            predict = self.model(features)
            loss = self.criterion(predict, labels)
            loss.backward()
            total_loss += loss.item()
            self.optimizer.step()
            print('\rBatch: %d of %d\tLoss: %4f' % (batch_idx + 1, len(self.train_iterator), total_loss / (batch_idx + 1)),
                  end='')
        print()

    def test_epoch(self):
        self.model.eval()
        total_loss = 0
        for batch_idx, (features, labels) in enumerate(self.test_iterator):
            predict = self.model(features)
            loss = self.criterion(predict, labels)
            total_loss += loss.item()
            print('\rBatch: %d of %d\tLoss: %4f' % (batch_idx + 1, len(self.test_iterator), total_loss / (batch_idx + 1)),
                  end='')
        print()
        loss = total_loss / len(self.test_iterator)
        return loss

    def train(self, num_epoches: int, patience: int):
        for epoch in range(num_epoches):
            print('Epoch %d' % (epoch + 1))
            self.train_epoch()
            test_loss = self.test_epoch()
            if test_loss < self.best_loss:
                print('New best loss on test: %4f' % test_loss)
                self.best_loss = test_loss
                self.best_epoch = epoch
                torch.save(self.model.state_dict(), self.model_name)
            else:
                print('Previous best epoch: %d' % (self.best_epoch + 1))
            if epoch - self.best_epoch > patience:
                print('Exiting by patience')
                break

