import random
import torch
from torch import nn as nn
from torch import optim as optim
from torch.utils.data import DataLoader
import numpy as np
import matplotlib.pyplot as plt
from tqdm import tqdm, trange
from torch.optim.lr_scheduler import ReduceLROnPlateau
import wandb
from nnfabrik.utility.nn_helpers import set_random_seed
import sys
#Set device

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

# Import functions and classes from the libraries
from neuralpredictors.measures.modules import PoissonLoss 
from modules_simple.Neural_Lib import *

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")



PROJECT = "V1 Training depth separable with factorized readout 10-07-2024"

# %%
def load_data():
    images_path_awake='/project/subiculum/data/images_uint8.npy'
    v1_responses_path_awake='/project/subiculum/data/V1_Data.mat'
    train_loader, val_loader, test_loader = dataloader_from_mat(images_path_awake, v1_responses_path_awake, 125, 200, 64)
    return train_loader, val_loader, test_loader

# %%
def fact_input_calc(input_kern, hidden_kern, layers, input_dim):
    dim=(input_dim+5-input_kern)-(layers-1)*(hidden_kern-5)
    return dim
def configure_model(config, n_neurons):
    model = DepthSepConvModel(layers=config.layers, 
                      input_kern=config.input_kern, 
                      hidden_kern=config.hidden_kern, 
                      hidden_channels=config.hidden_channels, 
                      #spatial_scale = config.spatial_scale, 
                      #std_scale = config.std_scale,
                      output_dim=n_neurons,
                      gaussian_readout=False,
                      reg_weight=config.reg_weight,
                      fact_input=fact_input_calc(config.input_kern, config.hidden_kern,config.layers,64))
    return model


def train(config, model, train_loader, val_loader, device):
    loss = PoissonLoss() # use different loss
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate)

    epochs = config.epochs
    log_epoch, vcorrs, tcorrs = [], [], []
    for epoch in trange(config.epochs):
        train_epoch(model, train_loader, optimizer, loss, device)
        if epoch % 5 == 0:
            log_epoch.append(epoch)
            train_corrs = get_correlations(model, train_loader, device)
            val_corrs = get_correlations(model, val_loader, device)
            vcorrs.append(val_corrs.mean())
            tcorrs.append(train_corrs.mean())
            
            print(f'Epoch [{epoch+1}/{epochs}], Validation correlation: {val_corrs.mean():.4f}, Training correlation: {train_corrs.mean():.4f}')
    train_corrs = get_correlations(model, train_loader, device)
    val_corrs = get_correlations(model, val_loader, device)

    return train_corrs, val_corrs, log_epoch, vcorrs, tcorrs


def objective(config, seed=42):
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    train_loader, val_loader, test_loader = load_data()
    set_random_seed(seed)
    model = configure_model(config, n_neurons=13) 
    model.to(device)

    train_corrs, val_corrs, log_epoch, vcorrs, tcorrs = train(config, model, train_loader, val_loader, device)

    return train_corrs, val_corrs, model, log_epoch, vcorrs, tcorrs

def objective_postsub(config, seed=42):
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    train_loader, val_loader, test_loader = load_data()
    set_random_seed(seed)
    model = configure_model(config, n_neurons=13) 
    model.to(device)

    train_corrs, val_corrs, log_epoch, vcorrs, tcorrs = train(config, model, train_loader, val_loader, device)

    return train_corrs, val_corrs, model, log_epoch, vcorrs, tcorrs


def main():
    """The `main` function initializes a Weights & Biases run, runs an objective function, and logs the
    validation score. This is used for hyperparameter optimization, but it requires a Weights & Biases
    account.
    """
    with wandb.init(project=PROJECT) as wdb_run:
        train_corrs, val_corrs, model, log_epoch, vcorrs, tcorrs = objective(wandb.config)
        wandb.log({"validation correlation": val_corrs.mean()})
        wandb.log({"train correlation": train_corrs.mean()})
        for epoch, vcorr, tcorr in zip(log_epoch, vcorrs, tcorrs):
            wandb.log({"val corr": vcorr, "epoch": epoch + 1})
            wandb.log({"train corr": tcorr, "epoch": epoch + 1})


# 2: Define the search space
sweep_configuration = {
    "method": "bayes",
    "name": "Gaussian Readout Hyperparameter Optimization",
    "metric": {"goal": "maximize", "name": "validation correlation"},
    "parameters": {
        "learning_rate": {"min": 1e-4, "max": 0.1},
        "layers": {"values": [1, 3, 5]},
        "epochs": {"values": [20, 30, 50, 100]},
        "input_kern": {"values": [5, 7, 11]},
        "hidden_kern": {"values": [5, 7, 11]},
        "hidden_channels": {"values": [32, 64, 128]},
        #"spatial_scale": {"min": 0.05, "max": 0.3},
        #"std_scale": {"min": 0.1, "max": 1.},
        "reg_weight":{"min":0.01,"max":3.0}
    },
}

if __name__ == "__main__":
    # 3: Start the sweep
    if len(sys.argv) == 1:
        sweep_id = wandb.sweep(sweep=sweep_configuration, project=PROJECT)
    else:
        sweep_id = sys.argv[1]
    wandb.agent(sweep_id, function=main, count=50, project=PROJECT)