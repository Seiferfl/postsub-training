import torch
import numpy as np
import scipy.io
import h5py
from torchvision.transforms import ToTensor, Normalize, Compose, Resize, ToPILImage, Pad, Grayscale
from torch.utils.data import Dataset, DataLoader, random_split
from torch.optim.lr_scheduler import ReduceLROnPlateau
import os
import torch.nn.functional as F
import torch.nn as nn
import warnings
from neuralpredictors.measures.modules import PoissonLoss
import seaborn as sns
from tqdm.notebook import trange
import matplotlib.pyplot as plt
from neuralpredictors.layers.readouts.factorized import FullFactorized2d

#This Library is supposed to include two DataLoader, one that can load images from .npy files for the Sensorium data, one that can load them from .mat files for the V1 and postsub data
# Also, it will include an implementation of the loss functions, models, correlation, oracle prediction and Gaussian readout which will be thoroughly documented, so people can actually understand what they are doing.
# Set the device
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


# DataSet

class NeuralDataset(Dataset):
    def __init__(self, images, responses, transform=None):
        """
        Args:
            images (Tensor): Images tensor [N, C, H, W]
            responses (Tensor): Responses tensor [N, Features]
            transform (callable, optional): Optional transform to be applied on an image.
        """
        self.images = images
        self.responses = responses
        self.transform = transform or Compose([
            ToPILImage(),
            Resize((64, 64)),  # Resize images to 64x64
            ToTensor(),        # Converts numpy.ndarray (H x W) to a torch.FloatTensor of shape (C x H x W)
            Normalize(mean=[0.456], std=[0.224])  # Adjust mean and std for single-channel
        ])  

    def __len__(self):
        return len(self.images)

    def __getitem__(self, idx):
        image = self.images[idx]
        response = self.responses[idx]
        if self.transform:
            image = self.transform(image)
        
        return image, response
    
class NeuralDatasetSensorium_Pretraining(Dataset):
    def __init__(self, images, responses, transform=None):
        """
        Args:
            images (Tensor): Images tensor [N, C, H, W]
            responses (Tensor): Responses tensor [N, Features]
            transform (callable, optional): Optional transform to be applied on an image.
        """
        self.images = images
        self.responses = responses
        self.transform = transform or Compose([
            ToPILImage(),
            Resize((72, 128)),  # Resize images to 64x64
            #Grayscale(num_output_channels=1),
            ToTensor(),        # Converts numpy.ndarray (H x W) to a torch.FloatTensor of shape (C x H x W)
            Normalize(mean=[0.456], std=[0.224])  # Adjust mean and std for single-channel
        ])  

    def __len__(self):
        return len(self.images)

    def __getitem__(self, idx):
        image = self.images[idx]
        response = self.responses[idx]
        if self.transform:
            image = self.transform(image)
        
        return image, response
    
class NeuralDatasetV1_Pretraining(Dataset):
    def __init__(self, images, responses, transform=None):
        """
        Args:
            images (Tensor): Images tensor [N, C, H, W]
            responses (Tensor): Responses tensor [N, Features]
            transform (callable, optional): Optional transform to be applied on an image.
        """
        self.images = images
        self.responses = responses
        self.transform = transform or Compose([
            ToPILImage(),
            Resize((60, 60)),  # Resize images to 60x60
            ToTensor(),        # Converts numpy.ndarray (H x W) to a torch.FloatTensor of shape (C x H x W)
            Pad((12, 34, 0, 34)),
            Normalize(mean=[0.456], std=[0.224])  # Adjust mean and std for single-channel
        ])  

    def __len__(self):
        return len(self.images)

    def __getitem__(self, idx):
        image = self.images[idx]
        response = self.responses[idx]
        if self.transform:
            image = self.transform(image)
            image=image.permute(0,2,1)
        return image, response

#Dataloaders

#Dataloader for .mat files

def load_mat_file(file_path):
    data = scipy.io.loadmat(file_path)
    responses = data['responses']
    stim_list = data['stim_list']
    binsize = data['binsize']
    return responses, stim_list, binsize

def preprocess_responses(responses, time_begin, time_end):
    responses=responses.astype(np.uint8)
    responses_p1 = torch.tensor(responses, dtype=torch.float32)
    responses_p2 = responses_p1.permute(1, 0, 2)
    responses_p3 = torch.sum(responses_p2[:,:,time_begin:time_end], dim=2)
    return responses_p3

# Load Images to np.array
def dataloader_from_mat(images_path, responses_path, time_begin, time_end, batch_size):
    images=np.load(images_path)
    # Load responses and preprocess them

    responses,_,_ = load_mat_file(responses_path)
    responses = preprocess_responses(responses, time_begin, time_end)

    #define Dataset
    train_ratio=0.6
    val_ratio=0.2

    dataset = NeuralDataset(images, responses)
    total_size = len(dataset)
    train_size = int(train_ratio * total_size)
    val_size = int(val_ratio * total_size)
    test_size = total_size - train_size - val_size
    train_dataset, val_dataset, test_dataset = random_split(dataset, [train_size, val_size, test_size])
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)
    return train_loader, val_loader, test_loader

def dataloader_from_mat_w_pad(images_path, responses_path, time_begin, time_end, batch_size):
    images=np.load(images_path)

    # Load responses and preprocess them

    responses,_,_ = load_mat_file(responses_path)
    responses = preprocess_responses(responses, time_begin, time_end)

    #define Dataset
    train_ratio=0.6
    val_ratio=0.2

    dataset = NeuralDatasetV1_Pretraining(images, responses)
    total_size = len(dataset)
    train_size = int(train_ratio * total_size)
    val_size = int(val_ratio * total_size)
    test_size = total_size - train_size - val_size
    train_dataset, val_dataset, test_dataset = random_split(dataset, [train_size, val_size, test_size])
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)
    return train_loader, val_loader, test_loader



# Second Dataloader

# Define a custom transform to apply average pooling and upscaling
"""
The following class is necessary for pretraining the model for the V1/postsub data with Sensorium data as the data from Sensorium is 256/144 and has to be brought to a square format.
"""

class CustomTransform:
    def __call__(self, img):
        # Ensure the image is a tensor
        if isinstance(img, np.ndarray):
            img = torch.tensor(img, dtype=torch.float32)
        elif isinstance(img, torch.Tensor):
            img = img.float()
        else:
            raise TypeError("Input should be a numpy array or torch tensor")

        # Ensure the image has only one channel
        if img.dim() == 2:
            img = img.unsqueeze(0)  # [H, W] -> [1, H, W]
        elif img.dim() == 3 and img.size(0) != 1:
            img = img.mean(dim=0, keepdim=True)  # Convert to grayscale

        # Apply average pooling to reduce 256 dimension to 128
        pooled_img = F.avg_pool2d(img, kernel_size=(1, 2))  # [1, 144, 128]

        # Interpolate to upscale to 64x64
        resized_img = F.interpolate(pooled_img.unsqueeze(0), size=(64, 64), mode='bilinear', align_corners=False)
        
        return resized_img.squeeze(0)  # Remove batch dimension if added
    

def dataloader_from_npy_pretraining(root_dir, device):
    # Initialize lists to collect data
    responses_list = []
    images_list = []

    # Load the data
    for n in range(5994):
        image_path = os.path.join(root_dir, 'images', f'{n}.npy')
        response_path = os.path.join(root_dir, 'responses', f'{n}.npy')
        
        response_data = np.load(response_path)
        image_data = np.load(image_path)
        
        # Ensure image is grayscale
        if image_data.ndim == 3 and image_data.shape[0] == 3:
            image_data = np.mean(image_data, axis=0, keepdims=True)
        
        responses_list.append(response_data)
        images_list.append(image_data)
        
    # Convert lists to NumPy arrays
    sensorium_responses = np.array(responses_list)
    sensorium_images = np.array(images_list).astype('uint8').squeeze()

    # Optionally convert to PyTorch tensors
    sensorium_responses_tensor = torch.tensor(sensorium_responses, device=device)
    #sensorium_images_tensor = torch.tensor(sensorium_images, device=device)

    # # Apply the custom transform
    # pretrain_transform = CustomTransform()
    # transformed_sensorium_images = torch.stack([pretrain_transform(img) for img in sensorium_images_tensor]).squeeze()
    # print(transformed_sensorium_images.shape)
    # transformed_sensorium_images_np = transformed_sensorium_images.cpu().numpy().astype('uint8')
    # print(f'transformed images in np shape {transformed_sensorium_images_np.shape}')

    pretrain_train_ratio=0.8
    pretrain_val_ratio=0.1
    pretrain_dataset = NeuralDatasetSensorium_Pretraining(sensorium_images, sensorium_responses_tensor)
    pretrain_total_size = len(pretrain_dataset)
    pretrain_train_size = int(pretrain_train_ratio * pretrain_total_size)
    pretrain_val_size = int(pretrain_val_ratio * pretrain_total_size)
    pretrain_test_size = pretrain_total_size - pretrain_train_size - pretrain_val_size
    pretrain_train_dataset, val_dataset, test_dataset = random_split(pretrain_dataset, [pretrain_train_size, pretrain_val_size, pretrain_test_size])
    pretrain_train_loader = DataLoader(pretrain_train_dataset, batch_size=64, shuffle=True)
    pretrain_val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
    pretrain_test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)
    return pretrain_train_loader, pretrain_val_loader, pretrain_test_loader

def dataloader_from_npy_pretraining_as_square(root_dir, device):
    # Initialize lists to collect data
    responses_list = []
    images_list = []

    # Load the data
    for n in range(5994):
        image_path = os.path.join(root_dir, 'images', f'{n}.npy')
        response_path = os.path.join(root_dir, 'responses', f'{n}.npy')
        
        response_data = np.load(response_path)
        image_data = np.load(image_path)
        
        # Ensure image is grayscale
        if image_data.ndim == 3 and image_data.shape[0] == 3:
            image_data = np.mean(image_data, axis=0, keepdims=True)
        
        responses_list.append(response_data)
        images_list.append(image_data)
        
    # Convert lists to NumPy arrays
    sensorium_responses = np.array(responses_list)
    sensorium_images = np.array(images_list).astype('uint8').squeeze()

    # Optionally convert to PyTorch tensors
    sensorium_responses_tensor = torch.tensor(sensorium_responses, device=device)
    #sensorium_images_tensor = torch.tensor(sensorium_images, device=device)

    # Apply the custom transform
    pretrain_transform = CustomTransform()
    transformed_sensorium_images = torch.stack([pretrain_transform(img) for img in sensorium_images_tensor]).squeeze()
    transformed_sensorium_images_np = transformed_sensorium_images.cpu().numpy().astype('uint8')
    print(f'transformed images in np shape {transformed_sensorium_images_np.shape}')
    pretrain_train_ratio=0.8
    pretrain_val_ratio=0.1
    pretrain_dataset = NeuralDataset(sensorium_images, sensorium_responses_tensor)
    pretrain_total_size = len(pretrain_dataset)
    pretrain_train_size = int(pretrain_train_ratio * pretrain_total_size)
    pretrain_val_size = int(pretrain_val_ratio * pretrain_total_size)
    pretrain_test_size = pretrain_total_size - pretrain_train_size - pretrain_val_size
    pretrain_train_dataset, val_dataset, test_dataset = random_split(pretrain_dataset, [pretrain_train_size, pretrain_val_size, pretrain_test_size])
    pretrain_train_loader = DataLoader(pretrain_train_dataset, batch_size=64, shuffle=True)
    pretrain_val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)
    pretrain_test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)
    return pretrain_train_loader, pretrain_val_loader, pretrain_test_loader



"""
In the next part of the code we define a correlation function, the Gaussian readout which is the last layer of the model, and the convolutional model.
"""

def corr(y1, y2, dim=-1, eps=1e-12, **kwargs):
    y1 = (y1 - y1.mean(axis=dim, keepdims=True)) / (y1.std(axis=dim, keepdims=True) + eps)
    y2 = (y2 - y2.mean(axis=dim, keepdims=True)) / (y2.std(axis=dim, keepdims=True) + eps)
    return (y1 * y2).mean(axis=dim, **kwargs)


class GaussianReadout(nn.Module):
    def __init__(self, output_dim, channels, spatial_scale, std_scale):
        super(GaussianReadout, self).__init__()
        self.pos_mean = nn.Parameter(torch.zeros(output_dim, 1, 2))
        self.pos_sqrt_cov = nn.Parameter(torch.zeros(output_dim, 2, 2))
        
        self.linear = nn.Parameter(torch.zeros(output_dim, channels))
        self.bias = nn.Parameter(torch.zeros(output_dim))
        
        self.pos_sqrt_cov.data.uniform_(-std_scale, std_scale)
        self.pos_mean.data.uniform_(-spatial_scale, spatial_scale)
        self.linear.data.fill_(1./channels)        
        
        
    def grid_positions(self, batch_size):
        if self.training:
            z = torch.randn(self.pos_mean.shape).to(self.pos_mean.device)
            grid = self.pos_mean + torch.einsum('nuk, njk->nuj', z, self.pos_sqrt_cov)
        else:
            grid = self.pos_mean
        grid = torch.clip(grid, -1, 1)
        return grid.expand(batch_size, -1, -1, -1) 
            
    def forward(self, x):
        batch_size = x.shape[0]
        grid = self.grid_positions(batch_size)
        
        # output will be batch_size x channels x neurons 
        x = torch.nn.functional.grid_sample(x, grid, align_corners=False).squeeze(-1)
        x = torch.einsum('bcn,nc->bn', x, self.linear) + self.bias.view(1, -1)
        return x


"""
Defining the model
"""
    
class ConvModel(nn.Module):
    def __init__(self, layers, input_kern, hidden_kern, hidden_channels, output_dim, spatial_scale = 0.1, std_scale = 0.5, gaussian_readout=True, reg_weight=None, fact_input=None):
        super(ConvModel, self).__init__()
        
        self.conv_layers = nn.Sequential()
        core_layers = [nn.Conv2d(1, hidden_channels, input_kern, padding=2), nn.BatchNorm2d(hidden_channels), nn.SiLU()]
        
        for _ in range(layers - 1):
            core_layers.extend([
                nn.Conv2d(hidden_channels, hidden_channels, hidden_kern, padding=2),
                nn.BatchNorm2d(hidden_channels),
                nn.SiLU()
            ]
            )
        self.core = nn.Sequential(*core_layers)
        
        # self.readout = FullGaussian2d((32, 18, 46), output_dim, bias=False)
        if gaussian_readout ==True:
            self.readout = GaussianReadout(output_dim, hidden_channels, spatial_scale=spatial_scale, std_scale=std_scale)
        else:
            self.readout = FullFactorized2d(
                in_shape=(hidden_channels, fact_input, fact_input),  # Set the appropriate shape
                outdims=output_dim,
                bias=True,
                spatial_and_feature_reg_weight=reg_weight or 1.0)
    
    def regularizer(self):
        if isinstance(self.readout, FullFactorized2d):
            return self.readout.regularizer()
        return self.readout.linear.abs().mean()
        
    def forward(self, x):
        x = self.core(x)
        x = self.readout(x)
        
        return nn.functional.softplus(x)
    
class DepthwiseSeparableConv(nn.Module):
    def __init__(self, n_in, n_out, kernel_size, padding=2):
        super(DepthwiseSeparableConv, self).__init__()
        self.depthwise = nn.Conv2d(n_in, n_in, kernel_size, padding=padding, groups=n_in)
        self.pointwise = nn.Conv2d(n_in, n_out, kernel_size= 1)
    def forward(self, x):
        out = self.depthwise(x)
        out = self.pointwise(out)
        return out
    
class DepthSepConvModel(nn.Module):
    def __init__(self, layers, input_kern, hidden_kern, hidden_channels, output_dim, spatial_scale = 0.1, std_scale = 0.5, gaussian_readout=True, reg_weight=None, fact_input=None):
        super(DepthSepConvModel, self).__init__()
        
        self.conv_layers = nn.Sequential
        core_layers = [nn.Conv2d(1, hidden_channels, input_kern, padding=2), nn.BatchNorm2d(hidden_channels), nn.SiLU()]
        
        for _ in range(layers - 1):
            core_layers.extend([
                DepthwiseSeparableConv(hidden_channels, hidden_channels, hidden_kern, padding=2),
                nn.BatchNorm2d(hidden_channels),
                nn.SiLU()
            ]
            )
        self.core = nn.Sequential(*core_layers)
        
        # self.readout = FullGaussian2d((32, 18, 46), output_dim, bias=False)
        if gaussian_readout ==True:
            self.readout = GaussianReadout(output_dim, hidden_channels, spatial_scale=spatial_scale, std_scale=std_scale)
        else:
            self.readout = FullFactorized2d(
                in_shape=(hidden_channels, fact_input, fact_input),  # Set the appropriate shape
                outdims=output_dim,
                bias=True,
                spatial_and_feature_reg_weight=reg_weight or 1.0)
    
    def regularizer(self):
        if isinstance(self.readout, FullFactorized2d):
            return self.readout.regularizer()
        return self.readout.linear.abs().mean()
        
    def forward(self, x):
        x = self.core(x)
        x = self.readout(x)
        
        return nn.functional.softplus(x)




def train_epoch(model, loader, optimizer, loss_fn, device):
    model.train()
    for images, responses in loader:
        images, responses = images.to(device), responses.to(device) 
        optimizer.zero_grad()
        outputs = model(images)
        loss = loss_fn(outputs, responses)
        loss.backward()
        optimizer.step()
    return loss

# train_epoch returns the loss of the last item in the batch, this seems very prone to errors due to outliers.

def my_train_epoch(model, loader, optimizer, loss_fn,device):
    model.train()
    train_loss = 0.0
    for images, responses in loader:
        images, responses = images.to(device), responses.to(device)  # Move data to the appropriate device
        optimizer.zero_grad()
        outputs = model(images)
        loss = loss_fn(outputs, responses)
        # if loss.item() < 0:
        #     print(f"Negative loss detected: {loss.item()}")
        #     print(f"Outputs: {outputs}")
        #     print(f"Responses: {responses}")
        loss.backward()
        optimizer.step()
        train_loss += loss.item()
    return train_loss / len(loader)

def train_readout(model, train_loader, val_loader, num_epochs, optimizer, loss_fn, device):
    model.eval()  # Make sure the core is in eval mode
    model.readout.train()  # Only train the readout

    early_stopping_patience = 5
    early_stopping_counter = 0
    best_val_loss = float('-inf')
    lr_scheduler = ReduceLROnPlateau(optimizer, mode='max', factor=0.1, patience=3, verbose=True)

    for epoch in trange(num_epochs):
        model.readout.train()
        train_loss = 0.0
        for images, responses in train_loader:
            images, responses = images.to(device), responses.to(device)
            optimizer.zero_grad()
            outputs = model(images)
            loss = loss_fn(outputs, responses)
            loss.backward()
            optimizer.step()
            train_loss += loss.item()
        train_loss /= len(train_loader)

        val_loss = 0.0
        model.readout.eval()
        with torch.no_grad():
            for images, responses in val_loader:
                images, responses = images.to(device), responses.to(device)
                outputs = model(images)
                loss = loss_fn(outputs, responses)
                val_loss += loss.item()
        val_loss /= len(val_loader)

        with torch.no_grad():
            val_corrs = get_correlations(model, val_loader, device)
            validation_correlation = val_corrs.mean()

        lr_scheduler.step(val_loss)

        print(f'Epoch [{epoch+1}/{num_epochs}], validation correlation: {validation_correlation:.4f}, trainloss: {train_loss:.4f}')

        if validation_correlation > best_val_loss:
            best_val_loss = validation_correlation
            early_stopping_counter = 0
        else:
            early_stopping_counter += 1
            if early_stopping_counter >= early_stopping_patience:
                print('Early stopping triggered!')
                break



def get_correlations(model, loader, device):
    """
    Calculates the correlation between the model's predictions and the actual responses.

    Args:
        model (torch.nn.Module): The trained model.
        loader (torch.utils.data.DataLoader): The data loader containing the images and responses.
        device (torch.device): The device to use for computation.

    Returns:
        float: The correlation between the model's predictions and the actual responses.
    """
    resp, pred = [], []
    model.eval()
    for images, responses in loader:
        images, responses = images.to(device), responses.to(device)  # Move data to the appropriate device
        outputs = model(images)
        resp.append(responses.cpu().detach().numpy())
        pred.append(outputs.cpu().detach().numpy())
    resp = np.vstack(resp)
    pred = np.vstack(pred)
    return corr(resp, pred, dim=0)



# Evaluation
def evaluate_model(model, test_loader, device):
    model.eval()
    test_loss = 0.0
    all_preds = []
    all_resps = []
    loss_fn = PoissonLoss()
    
    with torch.no_grad():
        for images, responses in test_loader:
            images, responses = images.to(device), responses.to(device)  # Move data to the appropriate device
            outputs = model(images)
            loss = loss_fn(outputs, responses)
            test_loss += loss.item()
            all_preds.append(outputs.cpu().numpy())
            all_resps.append(responses.cpu().numpy())
    
    test_loss /= len(test_loader)
    print(f'Test Loss: {test_loss:.4f}')
    
    all_preds = np.vstack(all_preds)
    all_resps = np.vstack(all_resps)
    correlation = get_correlations(model, test_loader, device)
    print(f'Test Correlation: {correlation.mean():.4f}')  # Print mean correlation
    
    return all_preds, all_resps


# Oracle

def oracle(model, model_state_path, device, test_loader):
    for x, y in test_loader:
        print(x.shape, y.shape)
        x = x.detach().cpu().numpy()
        print(np.abs(np.diff(x, axis=0)).max())
        break
    responses, oracle_predictor = [], []
    for _, y in test_loader:
        y = y.detach().cpu().numpy()
        responses.append(y)
        n = y.shape[0]
        trial_oracle = (n * np.mean(y, axis=0, keepdims=True) - y) / (n - 1)
        oracle_predictor.append(trial_oracle)
    responses = np.vstack(responses)
    oracle_predictor = np.vstack(oracle_predictor)
    oracle_correlation = corr(responses, oracle_predictor, dim=0)
    model= model
    state_dict=torch.load(model_state_path)
    model.load_state_dict(state_dict)
    model.to(device)
    with torch.no_grad():
        test_corrs = get_correlations(model, test_loader, device)
    sns.set_context('notebook', font_scale=1.5)
    print(test_corrs.shape)
    print(oracle_correlation.shape)
    with sns.axes_style('ticks'):
        fig, ax = plt.subplots(figsize=(8, 6))
    sns.histplot(test_corrs, kde=False, ax = ax, color=sns.xkcd_rgb['denim blue'], label='Test')
    sns.histplot(oracle_correlation, kde=False, ax = ax, color='deeppink', label='Oracle')
    ax.legend(frameon=False)
    sns.set_context('notebook', font_scale=1.5)
    with sns.axes_style('ticks'):
        fig, ax = plt.subplots(figsize=(8, 8))
    ax.scatter(oracle_correlation, test_corrs, s=3, color=sns.xkcd_rgb['cerulean'])
    ax.grid(True, linestyle='--', color='slategray')
    ax.plot([0, 1], [0, 1], color='black', linestyle='--')
    ax.set(
        xlabel='Oracle correlation',
        ylabel='Model correlation',
        xlim=[0, 1],
        ylim=[0, 1],
        aspect='equal',
    )


# def train_and_eval(model, epochs, train_loader, test_loader, val_loader, device,lr=1e-1, gamma=1e-3, save_model= False, path_for_saving=None, early_stopping=True):
#     # Define loss function and optimizer
#     poisson_loss = PoissonLoss()
#     loss_fn = lambda outputs, targets: poisson_loss(outputs, targets) + gamma * model.regularizer()
#     optimizer = torch.optim.Adam(model.parameters(), lr)

#     # Define the learning rate schedule
#     lr_scheduler = ReduceLROnPlateau(optimizer, mode='max', factor=0.1, patience=3, verbose=True)
#     early_stopping_patience = 5
#     early_stopping_counter = 0
#     best_val_loss = float('-inf')
#     for epoch in range(epochs):
#         # Training loop
#         loss = train_epoch(model, train_loader, optimizer, loss_fn, device)
        
#         # Validation loop
#         with torch.no_grad():
#             val_corrs = get_correlations(model, val_loader, device)
#         validation_correlation = val_corrs.mean()
        
#         # Update learning rate schedule
#         lr_scheduler.step(validation_correlation)
        
#         # Print training and validation losses
#         print(f'Epoch [{epoch+1}/{epochs}], validation correlation: {validation_correlation:.4f}, trainloss: {loss:.4f}')
        
#         # Check for early stopping
#         if early_stopping==True:
#             if validation_correlation > best_val_loss:
#                 best_val_loss = validation_correlation
#                 early_stopping_counter = 0
#             else:
#                 early_stopping_counter += 1
#                 if early_stopping_counter >= early_stopping_patience:
#                     print('Early stopping triggered!')
#                     break
#     if save_model==True:
#         torch.save(model.state_dict(), path_for_saving)
#         print("model saved as" + path_for_saving)
#     # Evaluate the model
#     evaluate_model(model, test_loader, device)


def find_duplicate_images(images):
    num_images = len(images)
    duplicates = []
    for i in range(num_images):
        n=0
        for j in range(i + 1, num_images):
            if np.array_equal(images[i], images[j]):
                duplicates.append((i, j))
                n=n+1
        print(f"{n} duplicates found for images {i}")        
    return duplicates



"""
Training functions:
"""

def evaluate_model(model, test_loader, device):
    model.eval()
    test_loss = 0.0
    all_preds = []
    all_resps = []
    loss_fn = PoissonLoss()
    
    with torch.no_grad():
        for images, responses in test_loader:
            images, responses = images.to(device), responses.to(device)  # Move data to the appropriate device
            outputs = model(images)
            loss = loss_fn(outputs, responses)
            test_loss += loss.item()
            all_preds.append(outputs.cpu().numpy())
            all_resps.append(responses.cpu().numpy())
    
    test_loss /= len(test_loader)
    print(f'Test Loss: {test_loss:.4f}')
    
    all_preds = np.vstack(all_preds)
    all_resps = np.vstack(all_resps)
    correlation = get_correlations(model, test_loader, device)
    print(f'Test Correlation: {correlation.mean():.4f}')  # Print mean correlation
    
    return all_preds, all_resps

# def training_and_eval(model, epochs, train_loader, test_loader, val_loader, device, save_model= False, path_for_saving=None, early_stopping=True):
#     # Define loss function and optimizer
#     poisson_loss = PoissonLoss()
#     gamma = 1e-3
#     loss_fn = lambda outputs, targets: poisson_loss(outputs, targets) + gamma * model.regularizer()
#     optimizer = torch.optim.Adam(model.parameters(), lr=1e-1)

#     # Define the learning rate schedule
#     lr_scheduler = ReduceLROnPlateau(optimizer, mode='max', factor=0.1, patience=3, verbose=True)
#     early_stopping_patience = 5
#     early_stopping_counter = 0
#     best_val_loss = float('-inf')
#     for epoch in range(epochs):
#         # Training loop
#         loss = train_epoch(model, train_loader, optimizer, loss_fn, device)
        
#         # Validation loop
#         with torch.no_grad():
#             val_corrs = get_correlations(model, val_loader, device)
#         validation_correlation = val_corrs.mean()
        
#         # Update learning rate schedule
#         lr_scheduler.step(validation_correlation)
        
#         # Print training and validation losses
#         print(f'Epoch [{epoch+1}/{epochs}], validation correlation: {validation_correlation:.4f}, trainloss: {loss:.4f}')
        
#         # Check for early stopping
#         if early_stopping==True:
#             if validation_correlation > best_val_loss:
#                 best_val_loss = validation_correlation
#                 early_stopping_counter = 0
#             else:
#                 early_stopping_counter += 1
#                 if early_stopping_counter >= early_stopping_patience:
#                     print('Early stopping triggered!')
#                     break
#     if save_model==True:
#         torch.save(model.state_dict(), path_for_saving)
#         print("model saved as" + path_for_saving)
#     # Evaluate the model
#     evaluate_model(model, test_loader, device)


def pretraining(model, train_loader, val_loader, epochs, optimizer, loss_fn, device):
    print(f'shape in dataloader {next(iter(pretrain_train_loader))[0].shape}')
    # Define early stopping criteria
    early_stopping_patience = 5
    early_stopping_counter = 0
    best_val_loss = float('-inf')
    pretrain_model = model.to(device)
    poisson_loss = PoissonLoss() # use different loss
    gamma = 1e-2
    #loss_fn = lambda outputs, targets: poisson_loss(outputs, targets) + gamma * pretrain_model.regularizer()
    #optimizer = torch.optim.Adam(pretrain_model.parameters(), lr=1e-3)
    # Define the learning rate schedule
    lr_scheduler = ReduceLROnPlateau(optimizer, mode='max', factor=0.1, patience=3, verbose=True)
    for epoch in range(epochs):
        # Training loop
        loss = my_train_epoch(pretrain_model, pretrain_train_loader, optimizer, loss_fn, device)
        
        # Validation loop
        with torch.no_grad():
            val_corrs = get_correlations(pretrain_model, pretrain_val_loader, device)
        validation_correlation = val_corrs.mean()
        
        # Update learning rate schedule
        lr_scheduler.step(validation_correlation)
        
        # Print training and validation losses
        print(f'Epoch [{epoch+1}/{epochs}], validation correlation: {validation_correlation:.4f}, trainloss: {loss:.4f}')
        
        # Check for early stopping
        if validation_correlation > best_val_loss:
            best_val_loss = validation_correlation
            early_stopping_counter = 0
        else:
            early_stopping_counter += 1
            if early_stopping_counter >= early_stopping_patience:
                print('Early stopping triggered!')
                break

    print('Freezing core.')

    for param in pretrain_model.core.parameters():
        param.requires_grad = False

def train_readout(model, train_loader, val_loader, num_epochs, optimizer, loss_fn, device):
    model.eval()  # Make sure the core is in eval mode
    model.readout.train()  # Only train the readout

    early_stopping_patience = 5
    early_stopping_counter = 0
    best_val_loss = float('-inf')
    lr_scheduler = ReduceLROnPlateau(optimizer, mode='max', factor=0.1, patience=3, verbose=True)

    for epoch in range(num_epochs):
        model.readout.train()
        train_loss = 0.0
        for images, responses in train_loader:
            images, responses = images.to(device), responses.to(device)
            optimizer.zero_grad()
            outputs = model(images)
            loss = loss_fn(outputs, responses)
            loss.backward()
            optimizer.step()
            train_loss += loss.item()
        train_loss /= len(train_loader)

        val_loss = 0.0
        model.readout.eval()
        with torch.no_grad():
            for images, responses in val_loader:
                images, responses = images.to(device), responses.to(device)
                outputs = model(images)
                loss = loss_fn(outputs, responses)
                val_loss += loss.item()
        val_loss /= len(val_loader)

        with torch.no_grad():
            val_corrs = get_correlations(model, val_loader, device)
            validation_correlation = val_corrs.mean()

        lr_scheduler.step(val_loss)

        print(f'Epoch [{epoch+1}/{num_epochs}], validation correlation: {validation_correlation:.4f}, trainloss: {train_loss:.4f}')

        if validation_correlation > best_val_loss:
            best_val_loss = validation_correlation
            early_stopping_counter = 0
        else:
            early_stopping_counter += 1
            if early_stopping_counter >= early_stopping_patience:
                print('Early stopping triggered!')
                break

def oracle(model, model_state_path, device, test_loader):
    for x, y in test_loader:
        print(x.shape, y.shape)
        x = x.detach().cpu().numpy()
        print(np.abs(np.diff(x, axis=0)).max())
        break
    responses, oracle_predictor = [], []
    for _, y in test_loader:
        y = y.detach().cpu().numpy()
        responses.append(y)
        n = y.shape[0]
        trial_oracle = (n * np.mean(y, axis=0, keepdims=True) - y) / (n - 1)
        oracle_predictor.append(trial_oracle)
    responses = np.vstack(responses)
    oracle_predictor = np.vstack(oracle_predictor)
    oracle_correlation = corr(responses, oracle_predictor, dim=0)
    model= model
    state_dict=torch.load(model_state_path)
    model.load_state_dict(state_dict)
    model.to(device)
    with torch.no_grad():
        test_corrs = get_correlations(model, test_loader, device)
    sns.set_context('notebook', font_scale=1.5)
    print(test_corrs.shape)
    print(oracle_correlation.shape)
    with sns.axes_style('ticks'):
        fig, ax = plt.subplots(figsize=(8, 6))
    sns.histplot(test_corrs, kde=False, ax = ax, color=sns.xkcd_rgb['denim blue'], label='Test')
    sns.histplot(oracle_correlation, kde=False, ax = ax, color='deeppink', label='Oracle')
    ax.legend(frameon=False)
    sns.set_context('notebook', font_scale=1.5)
    with sns.axes_style('ticks'):
        fig, ax = plt.subplots(figsize=(8, 8))
    ax.scatter(oracle_correlation, test_corrs, s=3, color=sns.xkcd_rgb['cerulean'])
    ax.grid(True, linestyle='--', color='slategray')
    ax.plot([0, 1], [0, 1], color='black', linestyle='--')
    ax.set(
        xlabel='Oracle correlation',
        ylabel='Model correlation',
        xlim=[0, 1],
        ylim=[0, 1],
        aspect='equal',
    )

def configure_model(config, n_neurons, device, gaussian_readout=True, reg_weight=None, fact_input=None):
    model = ConvModel(layers=config.get("layers"), 
                      input_kern=config.get("input_kern"), 
                      hidden_kern=config.get("hidden_kern"), 
                      hidden_channels=config.get("hidden_channels"), 
                      spatial_scale = config.get("spatial_scale"), 
                      std_scale = config.get("std_scale"),
                      output_dim=n_neurons,
                      gaussian_readout=gaussian_readout,
                      reg_weight=reg_weight,
                      fact_input=fact_input
                      )
    return model.to(device)

def configure_depth_sep_model(config, n_neurons, device, gaussian_readout=True, reg_weight=None, fact_input=None):
    model = DepthSepConvModel(layers=config.get("layers"), 
                      input_kern=config.get("input_kern"), 
                      hidden_kern=config.get("hidden_kern"), 
                      hidden_channels=config.get("hidden_channels"), 
                      spatial_scale = config.get("spatial_scale"), 
                      std_scale = config.get("std_scale"),
                      output_dim=n_neurons,
                      gaussian_readout=gaussian_readout,
                      reg_weight=reg_weight,
                      fact_input=fact_input
                      )
    return model.to(device)

def downscale_images_in_batches(images, batch_size=50):  # Reduced batch size
    downscaled_images = []
    num_batches = len(images) // batch_size + (1 if len(images) % batch_size != 0 else 0)
    
    for i in range(num_batches):
        batch = images[i * batch_size:(i + 1) * batch_size]
        batch_tensor = torch.tensor(batch, dtype=torch.float32).to(device)
        downscaled_batch = F.interpolate(batch_tensor.unsqueeze(1), size=(64, 64), mode='bilinear', align_corners=False)
        downscaled_batch = downscaled_batch.squeeze(1).cpu().numpy()
        downscaled_images.append(downscaled_batch)
        del batch_tensor, downscaled_batch  # Clear GPU memory
        torch.cuda.empty_cache()  # Free up unused memory
    
    return np.concatenate(downscaled_images, axis=0)

def sta_wo_model(device, best, data_path_1, data_path_2= None, data_is_npy = True, index_start=75, index_end=125):
    if data_is_npy==True:
        responses_list = []
        images_list = []
        # Load the data
        for n in range(5994):
            image_path = os.path.join(data_path_1, 'images', f'{n}.npy')
            response_path = os.path.join(data_path_1, 'responses', f'{n}.npy')
            
            response_data = np.load(response_path)
            image_data = np.load(image_path)
            
            # Ensure image is grayscale
            if image_data.ndim == 3 and image_data.shape[0] == 3:
                image_data = np.mean(image_data, axis=0, keepdims=True)
            
            responses_list.append(response_data)
            images_list.append(image_data)
            # Convert lists to NumPy arrays
        sensorium_responses = torch.tensor(np.array(responses_list).astype('float32')).to(device)
        sensorium_images = torch.tensor(np.array(images_list).astype('float32').squeeze()).to(device)
        print("Shows the STA for the best neurons (in terms of correlation on the validation loader):")
        fig, axs = plt.subplots(4,3, figsize=(15,9))
        axs=axs.flatten()
        print(sensorium_responses[:,best[0]].shape)
        for i in range (12):
            print(f"Processing neuron {best[i]}...")
            STA_img=torch.einsum('j,jkl->kl', sensorium_responses[:,best[i]]/torch.sum(sensorium_responses[:,i],axis=0),sensorium_images).cpu().numpy()
            im=axs[i].imshow(STA_img, cmap='gray')
            axs[i].set_title(f'STA neuron {best[i]}')
            axs[i].axis('off')
        for j in range(10, len(axs)):
            fig.delaxes(axs[j])
        fig.colorbar(im, ax=axs, orientation='horizontal', fraction=0.02, pad=0.04)
        plt.tight_layout()
        plt.show()

    else:
        responses,_,_=load_mat_file(data_path_1)
        responses = torch.tensor(np.sum(responses[:,:, index_start:index_end], axis=2).astype('float32')).to(device)
        images = torch.tensor(np.load(data_path_2)).to(device)
        images= torch.tensor(downscale_images_in_batches(images, batch_size=1)).to(device)
        print(images.shape)
        print(responses.shape)
        fig, axs = plt.subplots(4,3, figsize=(15,9))
        axs=axs.flatten()
        for i in range (12):
            im=axs[i].imshow(torch.einsum('j,jkl->kl', responses[best[i],:]/torch.sum(responses[best[i],:],axis=0),images).cpu().numpy(), cmap='gray')
            axs[i].set_title(f'STA neuron {best[i]}')
            axs[i].axis('off')
        for j in range(12, len(axs)):
            fig.delaxes(axs[j])
        plt.tight_layout()
        plt.show()

def STA_model_sens(model, model_path, best):
    state_dict = torch.load(model_path)
    model.load_state_dict(state_dict)
    model.to(device)
    imgs, responses = [], []
    idx = best[:12]
    for _ in range(1000):
        noise = torch.randn(100, 1, 36, 64).to(device)
        with torch.no_grad():
            resp = model(noise).cpu().numpy()[:, idx]
        responses.append(resp)
        imgs.append(noise.cpu().numpy())
    imgs = np.concatenate(imgs, axis=0)
    responses = np.concatenate(responses, axis=0)
    responses = (responses - responses.mean(axis=0)) / responses.std(axis=0)
    stas = (imgs * responses[..., None, None]).mean(axis=0)

    with sns.axes_style('white'):
        fig, ax = plt.subplots(4, 3, figsize=(16, 12))
        for i, sta, a in zip(best, stas, ax.flatten()):
            a.imshow(sta.squeeze(), cmap='gray')
            a.axis('off')
            a.set_title(f'STA neuron {i}')

def STA_model(model, model_path, best):
    state_dict = torch.load(model_path)
    model.load_state_dict(state_dict)
    model.to(device)
    imgs, responses = [], []
    idx = best[:12]
    for _ in range(1000):
        noise = torch.randn(100, 1, 64, 64).to(device)
        with torch.no_grad():
            resp = model(noise).cpu().numpy()[:, idx]
        responses.append(resp)
        imgs.append(noise.cpu().numpy())
    imgs = np.concatenate(imgs, axis=0)
    responses = np.concatenate(responses, axis=0)
    responses = (responses - responses.mean(axis=0)) / responses.std(axis=0)
    stas = (imgs * responses[..., None, None]).mean(axis=0)

    with sns.axes_style('white'):
        fig, ax = plt.subplots(4, 3, figsize=(16, 12))
        for i, sta, a in zip(best, stas, ax.flatten()):
            a.imshow(sta.squeeze(), cmap='gray')
            a.axis('off')
            a.set_title(f'STA neuron {i}')

def get_last_conv_out_channels(model):
    for layer in reversed(model.core):
        if isinstance(layer, nn.Conv2d):
            return layer.out_channels
    raise ValueError("No Conv2d layer found in the model.")


def get_last_conv_output_shape(model, input_shape):
    def get_output_shape(layer, input_shape):
        dummy_input = torch.zeros(*input_shape)
        dummy_output = layer(dummy_input)
        return dummy_output.shape
    
    last_conv_layer = None
    for layer in model.core:
        if isinstance(layer, nn.Conv2d):
            last_conv_layer = layer
    
    if last_conv_layer is None:
        raise ValueError("No Conv2d layer found in the model.")
    
    return get_output_shape(last_conv_layer, input_shape)

def gradientRF(model, model_state_path, best_neurons, device):
    state_dict = torch.load(model_state_path)
    model.load_state_dict(state_dict)
    model=model.to(device)
    rfs = []
    for i in best_neurons[:12]:
        x = torch.zeros(1,1,64,64).to(device)
        x.requires_grad = True
        r = model(x)
        r[0,i].backward()
        rfs.append(x.grad.cpu().numpy().squeeze())
    
    with sns.axes_style('white'):
        fig, ax = plt.subplots(4, 3, figsize=(12,12))
        for i, rf, a in zip(range(len(rfs)), rfs, ax.flatten()):
            a.imshow(rf.squeeze(), cmap = 'gray')
            a.axis('off')
            a.set_title(f'RF neuron {best_neurons[i]}')


def plot_nth_image(train_loader,n):
    for images, _ in train_loader:
        first_image = images[n].cpu().numpy().squeeze()
        plt.imshow(first_image, cmap='gray')
        plt.title("First Image in the Train Loader")
        plt.show()
        break

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
def plot_avrg_response(images_path, response_path):
    # Load Images to np.array
    images = np.load(images_path)

    # Load responses and preprocess them
    responses, _, _ = load_mat_file(response_path)
    responses = np.transpose(responses, (1, 2, 0))

    # Look at mean data for neurons
    responses_mean = np.mean(responses, axis=0, keepdims=True)

    # Define the number of plots
    num_plots = responses.shape[2]
    neuron_indices = range(num_plots)

    # Create a 3x5 grid of subplots
    fig, axs = plt.subplots(int(np.ceil(float(num_plots)/5.0)),5, figsize=(15, 9))
    axs = axs.flatten()  # Flatten the 2D array to 1D for easier iteration

    # Create a custom legend patch
    legend_patch = mpatches.Patch(color='gray', alpha=0.5, label='Image shown')

    # Plot each neuron data
    x = np.arange(0, 2000, 10)
    for i, neuron_index in enumerate(neuron_indices):
        if i < len(neuron_indices):  # Ensure we don't go out of bounds
            neuron_mean = responses_mean[:, :, neuron_index]
            axs[i].plot(x, neuron_mean[0, :])
            axs[i].axvspan(750, 1250, color='gray', alpha=0.5)  # Add transparent gray tile
            axs[i].set_title(f'Neuron {neuron_index + 1}')
            axs[i].set_xlabel('Time (ms)')
            axs[i].set_ylabel('Response')
            # Add the legend to the first plot only to avoid repetition
            if i == 0:
                axs[i].legend(handles=[legend_patch])

    # Remove empty subplots (if any)
    for j in range(len(neuron_indices), len(axs)):
        fig.delaxes(axs[j])

    # Adjust layout
    plt.tight_layout()
    plt.show()