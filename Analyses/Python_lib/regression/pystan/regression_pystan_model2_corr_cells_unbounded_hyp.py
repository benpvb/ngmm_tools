#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Jul 13 18:22:15 2021

@author: glavrent
"""

#load variables
import os
import pathlib
import glob
import re           #regular expression package
import pickle
from joblib import cpu_count
#arithmetic libraries
import numpy as np
#statistics libraries
import pandas as pd
#plot libraries
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.ticker import  AutoLocator as plt_autotick
import arviz as az
mpl.use('agg')

def RunStan(df_flatfile, df_cellinfo, df_celldist, stan_model_fname, 
            out_fname, out_dir, res_name='res', c_a_erg=0, 
            runstan_flag=True,
            n_iter=600, n_chains=4,
            adapt_delta=0.8, max_treedepth=10,
            pystan_ver=2, pystan_parallel=False):
    '''
    Run full Bayessian regression in Stan. Non-ergodic model includes: a spatially
    varying earthquake constant, a spatially varying site constant, a spatially 
    independent site constant, and partially spatially correlated anelastic 
    attenuation.

    Parameters
    ----------
    df_flatfile : pd.DataFrame
        Input data frame containing total residuals, eq and site coordinates.
    df_cellinfo : pd.DataFrame
        Dataframe with coordinates of anelastic attenuation cells.
    df_celldist : pd.DataFrame
        Datafame with cell path distances of all records in df_flatfile.
    stan_model_fname : string
        File name for stan model.
    out_fname : string
        File name for output files.
    out_dir : string
        Output directory.
    res_name : string, optional
        Column name for total residuals. The default is 'res'.
    c_a_erg : double, optional
        Value of ergodic anelatic attenuation coefficient. Used as mean of cell
        specific anelastic attenuation prior distribution. The default is 0.
    n_iter : integer, optional
        Number of stan samples. The default is 600.
    n_chains : integer, optional
        Number of MCMC chains. The default is 4.
    runstan_flag : bool, optional
        Flag for running stan. If true run regression, if false read past regression
        output and summarize non-ergodic parameters. The default is True.
    adapt_delta : double, optional
        Target average proposal acceptance probability for adaptation. The default is 0.8.
    max_treedepth : integer, optional
        Maximum number of evaluations for each iteration (2^max_treedepth). The default is 10.
    pystan_ver : integer, optional
        Version of pystan to run. The default is 2.
    pystan_parallel : bool, optional
        Flag for using multithreaded option in STAN. The default is False.

    Returns
    -------
    None.

    '''

    ## Read Data
    #============================
    #read stan model
    with open(stan_model_fname, "r") as f:
        stan_model_code = f.read()
    
    ## Preprocess Input Data
    #============================
    #set rsn column as dataframe index, skip if rsn already the index
    if not df_flatfile.index.name == 'rsn':
        df_flatfile.set_index('rsn', drop=True, inplace=True)
    if not df_celldist.index.name == 'rsn':
        df_celldist.set_index('rsn', drop=True, inplace=True)
    #set cellid column as dataframe index, skip if cellid already the index    
    if not df_cellinfo.index.name == 'cellid':
        df_cellinfo.set_index('cellid', drop=True, inplace=True)
    
    # number of data
    n_data = len(df_flatfile)

    # earthquake data
    data_eq_all = df_flatfile[['eqid','mag','eqX', 'eqY']].values
    _, eq_idx, eq_inv = np.unique(df_flatfile[['eqid']], axis=0, return_inverse=True, return_index=True)
    data_eq = data_eq_all[eq_idx,:]
    X_eq = data_eq[:,[2,3]] #earthquake coordinates
    #create earthquake ids for all records (1 to n_eq)
    eq_id = eq_inv + 1
    n_eq = len(data_eq)

    #station data
    data_sta_all = df_flatfile[['ssn','Vs30','staX','staY']].values
    _, sta_idx, sta_inv = np.unique( df_flatfile[['ssn']].values, axis = 0, return_inverse=True, return_index=True)
    data_sta = data_sta_all[sta_idx,:]
    X_sta = data_sta[:,[2,3]] #station coordinates
    #create station indices for all records (1 to n_sta)
    sta_id = sta_inv + 1
    n_sta = len(data_sta)
    #ground-motion observations  
    y_data = df_flatfile[res_name].to_numpy().copy()
    
    #cell data
    #reorder and only keep records included in the flatfile
    df_celldist = df_celldist.reindex(df_flatfile.index)
    
    #cell info
    cell_ids_all   = df_cellinfo.index
    cell_names_all = df_cellinfo.cellname
    #cell distance matrix
    celldist_all  = df_celldist[cell_names_all]               #cell-distance matrix with all cells
    #find cell with more than one paths
    i_cells_valid = np.where(celldist_all.sum(axis=0) > 0)[0] #valid cells with more than one path
    cell_ids_valid   = cell_ids_all[i_cells_valid]
    cell_names_valid = cell_names_all[i_cells_valid]
    celldist_valid   = celldist_all.loc[:,cell_names_valid] #cell-distance with only non-zero cells
    # number of cells
    n_cell = celldist_all.shape[1]
    n_cell_valid = celldist_valid.shape[1]
    # cell coordinates
    X_cells_valid = df_cellinfo.loc[i_cells_valid,['mptX','mptY']].values

    # print Rrup missfits
    print('max R_rup misfit', (df_flatfile.Rrup.values - celldist_valid.sum(axis=1)).abs().max())
    stan_data = {'N':        n_data,
                 'NEQ':      n_eq,
                 'NSTAT':    n_sta,
                 'NCELL':    n_cell_valid,
                 'eq':       eq_id,                  #earthquake id
                 'stat':     sta_id,                 #station id
                 'X_e':      X_eq,                   #earthquake coordinates
                 'X_s':      X_sta,                  #station coordinates
                 'X_c':      X_cells_valid,
                 'rec_mu':   np.zeros(y_data.shape),
                 'RC':       celldist_valid.to_numpy(),
                 'c_a_erg':  c_a_erg,
                 'Y':        y_data,
                }
    stan_data_fname = out_fname + '_stan_data' + '.Rdata'
    
    ## Run Stan, fit model 
    #============================
    #number of cores
    n_cpu = max(cpu_count() -1,1)
    
    #filename for STAN regression raw output file saved as pkl
    stan_fit_fname = out_dir  + out_fname + '_stan_fit' + '.pkl' 

    #run stan
    if runstan_flag:
        #control paramters
        control_stan = {'adapt_delta':adapt_delta, 'max_treedepth':max_treedepth}
        if pystan_ver == 2:
            import pystan
            if (not pystan_parallel) or n_cpu<=n_chains:
                #compile 
                stan_model = pystan.StanModel(model_code=stan_model_code)
                # full Bayesian statistics
                stan_fit = stan_model.sampling(data=stan_data, iter=n_iter, chains = n_chains, refresh=10, control = control_stan)
            else:   
                #number of cores per chain
                n_cpu_chain = int(np.floor(n_cpu/n_chains))
                #multi-processing arguments
                os.environ['STAN_NUM_THREADS'] = str(n_cpu_chain)
                extra_compile_args = ['-pthread', '-DSTAN_THREADS']
                #compile 
                stan_model = pystan.StanModel(model_code=stan_model_code, extra_compile_args=extra_compile_args)
                # full Bayesian statistics
                stan_fit = stan_model.sampling(data=stan_data, iter=n_iter, chains = n_chains, refresh=1, control = control_stan)
        elif pystan_ver == 3:
            import nest_asyncio
            import stan
            nest_asyncio.apply()
            # compile 
            stan_model = stan.build(stan_model_code, data=stan_data, random_seed=1)
            # full Bayesian statistics
            stan_fit = stan_model.sample(num_chains=n_chains, num_samples=n_iter, max_depth=max_treedepth, delta=adapt_delta)
        
        # save stan model and fit
        pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True) 
        with open(stan_fit_fname, "wb") as f:
            pickle.dump({'model' : stan_model, 'fit' : stan_fit}, f, protocol=-1)
    else:
        #load model and fit for postprocessing if has already been executed
        with open(stan_fit_fname, "rb") as f:
            data_dict = pickle.load(f)
        stan_fit = data_dict['fit']
        stan_model = data_dict['model']
        del data_dict

    ## Postprocessing Data
    #============================
    ## Extract posterior samples
    # ---------------------------
    # hyper-parameters
    col_names_hyp = ['dc_0','ell_1e', 'ell_1as', 'omega_1e', 'omega_1as', 'omega_1bs',
                     'mu_cap', 'ell_ca1p', 'omega_ca1p', 'omega_ca2p',
                     'phi_0','tau_0']

    # non-ergodic terms
    col_names_dc_1e  = ['dc_1e.%i'%(k)    for k in range(n_eq)]
    col_names_dc_1as = ['dc_1as.%i'%(k)   for k in range(n_sta)]
    col_names_dc_1bs = ['dc_1bs.%i'%(k)   for k in range(n_sta)]
    col_names_dB     = ['dB.%i'%(k)       for k in range(n_eq)]
    col_names_cap    = ['c_cap.%i'%(c_id) for c_id in cell_ids_valid]
    col_names_all = col_names_hyp + col_names_dc_1e + col_names_dc_1as + col_names_dc_1bs + col_names_cap + col_names_dB
    
    # summarize raw posterior distributions
    stan_posterior = np.stack([stan_fit[c_n].flatten() for c_n in col_names_hyp], axis=1)
    # adjustment terms 
    if pystan_ver == 2:
        stan_posterior = np.concatenate((stan_posterior, stan_fit['dc_1e']),  axis=1)
        stan_posterior = np.concatenate((stan_posterior, stan_fit['dc_1as']), axis=1)
        stan_posterior = np.concatenate((stan_posterior, stan_fit['dc_1bs']), axis=1)
        stan_posterior = np.concatenate((stan_posterior, stan_fit['c_cap']),  axis=1)
        stan_posterior = np.concatenate((stan_posterior, stan_fit['dB']),     axis=1)
    else:
        stan_posterior = np.concatenate((stan_posterior, stan_fit['dc_1e'].T),  axis=1)
        stan_posterior = np.concatenate((stan_posterior, stan_fit['dc_1as'].T), axis=1)
        stan_posterior = np.concatenate((stan_posterior, stan_fit['dc_1bs'].T), axis=1)
        stan_posterior = np.concatenate((stan_posterior, stan_fit['c_cap'].T),  axis=1)
        stan_posterior = np.concatenate((stan_posterior, stan_fit['dB'].T),     axis=1)
    
    # save raw-posterior distribution
    df_stan_posterior_raw = pd.DataFrame(stan_posterior, columns = col_names_all)
    df_stan_posterior_raw.to_csv(out_dir + out_fname + '_stan_posterior_raw' + '.csv', index=False)
    
    ## Summarize hyper-parameters
    # ---------------------------
    #summarize posterior distributions of hyper-parameters
    perc_array = np.array([0.05,0.25,0.5,0.75,0.95])
    df_stan_hyp = df_stan_posterior_raw[col_names_hyp].quantile(perc_array)
    df_stan_hyp = df_stan_hyp.append(df_stan_posterior_raw[col_names_hyp].mean(axis = 0), ignore_index=True)
    df_stan_hyp.index = ['prc_%.2f'%(prc) for prc in perc_array]+['mean'] 
    df_stan_hyp.to_csv(out_dir + out_fname + '_stan_hyperparameters' + '.csv', index=True)
    
    #detailed posterior percentiles of posterior distributions
    perc_array = np.arange(0.01,0.99,0.01)    
    df_stan_posterior = df_stan_posterior_raw[col_names_hyp].quantile(perc_array)
    df_stan_posterior.index.name = 'prc'
    df_stan_posterior.to_csv(out_dir + out_fname + '_stan_hyperposterior' + '.csv', index=True)
    
    del col_names_dc_1e, col_names_dc_1as, col_names_dc_1bs, col_names_dB
    del stan_posterior, col_names_all
    
    ## Sample spatially varying coefficients and predictions at record locations
    # ---------------------------
    # earthquake and station location in database
    X_eq_all  = df_flatfile[['eqX', 'eqY']].values
    X_sta_all = df_flatfile[['staX','staY']].values    
    
    # GMM anelastic attenuation
    #---  ---  ---  ---  ---  ---  ---  ---
    cells_ca_mu  = np.array([df_stan_posterior_raw.loc[:,'c_cap.%i'%(k)].mean()   for k in cell_ids_valid])
    cells_ca_med = np.array([df_stan_posterior_raw.loc[:,'c_cap.%i'%(k)].median() for k in cell_ids_valid])
    cells_ca_sig = np.array([df_stan_posterior_raw.loc[:,'c_cap.%i'%(k)].std()    for k in cell_ids_valid])
    
    # effect of anelastic attenuation in GM
    cells_LcA_mu  = celldist_valid.values @ cells_ca_mu
    cells_LcA_med = celldist_valid.values @ cells_ca_med
    cells_LcA_sig = np.sqrt(np.square(celldist_valid.values) @ cells_ca_sig**2)
    
    # summary attenuation cells
    catten_summary = np.vstack((np.tile(c_a_erg,  n_cell_valid),
                                cells_ca_mu,
                                cells_ca_med,
                                cells_ca_sig)).T
    columns_names = ['c_a_erg','c_cap_mean','c_cap_med','c_cap_sig']
    df_catten_summary = pd.DataFrame(catten_summary, columns = columns_names, index=df_cellinfo.index[i_cells_valid])
    #create dataframe with summary attenuation cells
    df_catten_summary = pd.merge(df_cellinfo[['cellname','mptLat','mptLon','mptX','mptY','mptZ','UTMzone']], 
                                 df_catten_summary, how='right', left_index=True, right_index=True)
    df_catten_summary.to_csv(out_dir + out_fname + '_stan_catten' + '.csv', index=True)
    
    # GMM coefficients
    #---  ---  ---  ---  ---  ---  ---  ---
    # constant shift coefficient
    coeff_0_mu  = df_stan_posterior_raw.loc[:,'dc_0'].mean()   * np.ones(n_data)
    coeff_0_med = df_stan_posterior_raw.loc[:,'dc_0'].median() * np.ones(n_data)
    coeff_0_sig = df_stan_posterior_raw.loc[:,'dc_0'].std()    * np.ones(n_data)
    
    # spatially varying earthquake constant coefficient
    coeff_1e_mu  = np.array([df_stan_posterior_raw.loc[:,f'dc_1e.{k}'].mean()   for k in range(n_eq)])
    coeff_1e_mu  = coeff_1e_mu[eq_inv]
    coeff_1e_med = np.array([df_stan_posterior_raw.loc[:,f'dc_1e.{k}'].median() for k in range(n_eq)])
    coeff_1e_med = coeff_1e_med[eq_inv]
    coeff_1e_sig = np.array([df_stan_posterior_raw.loc[:,f'dc_1e.{k}'].std()    for k in range(n_eq)])
    coeff_1e_sig = coeff_1e_sig[eq_inv]
    
    #site term constant covariance
    coeff_1as_mu  = np.array([df_stan_posterior_raw.loc[:,f'dc_1as.{k}'].mean()   for k in range(n_sta)])
    coeff_1as_mu  = coeff_1as_mu[sta_inv]
    coeff_1as_med = np.array([df_stan_posterior_raw.loc[:,f'dc_1as.{k}'].median() for k in range(n_sta)])
    coeff_1as_med = coeff_1as_med[sta_inv]
    coeff_1as_sig = np.array([df_stan_posterior_raw.loc[:,f'dc_1as.{k}'].std()    for k in range(n_sta)])
    coeff_1as_sig = coeff_1as_sig[sta_inv]
    
    #spatially varying station constant covariance
    coeff_1bs_mu  = np.array([df_stan_posterior_raw.loc[:,f'dc_1bs.{k}'].mean()   for k in range(n_sta)])
    coeff_1bs_mu  = coeff_1bs_mu[sta_inv]
    coeff_1bs_med = np.array([df_stan_posterior_raw.loc[:,f'dc_1bs.{k}'].median() for k in range(n_sta)])
    coeff_1bs_med = coeff_1bs_med[sta_inv]
    coeff_1bs_sig = np.array([df_stan_posterior_raw.loc[:,f'dc_1bs.{k}'].std()    for k in range(n_sta)])
    coeff_1bs_sig = coeff_1bs_sig[sta_inv]
    
    # aleatory variability
    phi_0_array = np.array([df_stan_posterior_raw.phi_0.mean()]*X_sta_all.shape[0])
    tau_0_array = np.array([df_stan_posterior_raw.tau_0.mean()]*X_sta_all.shape[0])
    
    #initiaize flatfile for sumamry of non-erg coefficinets and residuals
    df_flatinfo = df_flatfile[['eqid','ssn','eqLat','eqLon','staLat','staLon','eqX','eqY','staX','staY','UTMzone']]
    
    #summary coefficients
    coeffs_summary = np.vstack((coeff_0_mu,
                                coeff_1e_mu, 
                                coeff_1as_mu,
                                coeff_1bs_mu,
                                cells_LcA_mu,
                                coeff_0_med,
                                coeff_1e_med, 
                                coeff_1as_med,
                                coeff_1bs_med,
                                cells_LcA_med,
                                coeff_0_sig,
                                coeff_1e_sig, 
                                coeff_1as_sig,
                                coeff_1bs_sig,
                                cells_LcA_sig)).T
    columns_names = ['dc_0_mean','dc_1e_mean','dc_1as_mean','dc_1bs_mean','Lc_ca_mean',
                     'dc_0_med', 'dc_1e_med', 'dc_1as_med', 'dc_1bs_med', 'Lc_ca_med',
                     'dc_0_sig', 'dc_1e_sig', 'dc_1as_sig', 'dc_1bs_sig', 'Lc_ca_sig']
    df_coeffs_summary = pd.DataFrame(coeffs_summary, columns = columns_names, index=df_flatfile.index)
    #create dataframe with summary coefficients
    df_coeffs_summary = pd.merge(df_flatinfo, df_coeffs_summary, how='right', left_index=True, right_index=True)
    df_coeffs_summary[['eqid','ssn']] = df_coeffs_summary[['eqid','ssn']].astype(int)
    df_coeffs_summary.to_csv(out_dir + out_fname + '_stan_coefficients' + '.csv', index=True)

    # GMM prediction
    #---  ---  ---  ---  ---  ---  ---  ---
    #mean prediction
    y_mu  = (coeff_0_mu + coeff_1e_mu + coeff_1as_mu + coeff_1bs_mu + cells_LcA_mu)

    #compute residuals
    res_tot     = y_data - y_mu
    #residuals computed directly from stan regression
    res_between = [df_stan_posterior_raw.loc[:,f'dB.{k}'].mean() for k in range(n_eq)]
    res_between = np.array([res_between[k] for k in (eq_inv).astype(int)])
    res_within  = res_tot - res_between

    #summary predictions and residuals
    predict_summary = np.vstack((y_mu, res_tot, res_between, res_within)).T
    columns_names = ['nerg_mu','res_tot','res_between','res_within']
    df_predict_summary = pd.DataFrame(predict_summary, columns = columns_names, index=df_flatfile.index)
    #create dataframe with predictions and residuals
    df_predict_summary = pd.merge(df_flatinfo, df_predict_summary, how='right', left_index=True, right_index=True)
    df_predict_summary[['eqid','ssn']] = df_predict_summary[['eqid','ssn']].astype(int)
    df_predict_summary.to_csv(out_dir  + out_fname + '_stan_residuals' + '.csv', index=True)

    ## Summary regression
    # ---------------------------
    #save summary statistics
    stan_summary_fname = out_dir  + out_fname + '_stan_summary' + '.txt'
    with open(stan_summary_fname, 'w') as f:
        print(stan_fit, file=f)
    
    #create and save trace plots
    fig_dir = out_dir  + 'summary_figs/'
    #create figures directory if doesn't exit
    pathlib.Path(fig_dir).mkdir(parents=True, exist_ok=True) 
    
    #create stan trace plots
    for c_name in col_names_hyp:
        #create trace plot with arviz
        ax = az.plot_trace(stan_fit,  var_names=c_name, figsize=(10,5) ).ravel()
        ax[0].yaxis.set_major_locator(plt_autotick())
        ax[0].set_xlabel('sample value')
        ax[0].set_ylabel('frequency')
        ax[0].set_title('')
        ax[0].grid(axis='both')
        ax[1].set_xlabel('iteration')
        ax[1].set_ylabel('sample value')
        ax[1].grid(axis='both')
        ax[1].set_title('')
        fig = ax[0].figure
        fig.suptitle(c_name)
        fig.savefig(fig_dir + out_fname + '_stan_traceplot_' + c_name + '_arviz' + '.png')

    return None
