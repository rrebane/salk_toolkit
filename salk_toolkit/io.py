# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_io.ipynb.

# %% auto 0
__all__ = ['custom_meta_key', 'read_json', 'read_annotated_data', 'extract_column_meta', 'group_columns_dict', 'list_aliases',
           'change_meta_df', 'change_parquet_meta', 'read_and_process_data', 'save_population_h5', 'load_population_h5',
           'save_sample_h5', 'save_parquet_with_metadata', 'load_parquet_with_metadata', 'load_parquet_metadata']

# %% ../nbs/01_io.ipynb 3
import json, os
import itertools as it
from collections import defaultdict

import numpy as np
import pandas as pd
import datetime as dt

from typing import List, Tuple, Dict, Union, Optional

import pyarrow as pa
import pyarrow.parquet as pq
import pyreadstat

import salk_toolkit as stk
from salk_toolkit.utils import replace_constants, vod

# %% ../nbs/01_io.ipynb 4
def read_json(fname,replace_const=True):
    with open(fname,'r') as jf:
        meta = json.load(jf)
    if replace_const:
        meta = replace_constants(meta)
    return meta

# %% ../nbs/01_io.ipynb 5
# Default usage with mature metafile: read_annotated_data(<metafile name>)
# When figuring out the metafile, it can also be run as: read_annotated_data(meta=<dict>, data_file=<>)
def read_annotated_data(meta_fname=None, multilevel=False, meta=None, data_file=None, return_meta=False):
    
    # Read metafile
    if meta_fname:
        meta = read_json(meta_fname,replace_const=False)
    
    # Setup constants with a simple replacement mechanic
    constants = meta['constants'] if 'constants' in meta else {}
    meta = replace_constants(meta)
    
    # Read datafile
    if not data_file:
        data_file = os.path.join(os.path.dirname(meta_fname),meta['file'])
    opts = meta['read_opts'] if'read_opts' in meta else {}
    
    if data_file[-3:] == 'csv':
        raw_data = pd.read_csv(data_file, **opts)
    elif data_file[-3:] == 'sav':
        raw_data, _ = pyreadstat.read_sav(data_file, **{ 'apply_value_formats':True, 'dates_as_pandas_datetime':True },**opts)
    elif data_file[-7:] == 'parquet':
        raw_data = pd.read_parquet(data_file, **opts)
    else:
        raise Exception(f"Not a known file format {data_file}")
    
    res = []
    
    if 'preprocessing' in meta:
        exec(meta['preprocessing'],{'pd':pd, 'np':np, 'stk':stk, 'df':raw_data, **constants })
        
    for group in meta['structure']:
        gres = []
        for tpl in group['columns']:
            if type(tpl)==list:
                cn = tpl[0] # column name
                sn = tpl[1] if type(tpl[1])==str else cn # source column
                cd = tpl[2] if len(tpl)==3 else tpl[1] if type(tpl[1])==dict else {} # metadata
            else:
                cn = sn = tpl
                cd = {}

            if 'scale' in group: cd = {**group['scale'],**cd}
            
            if sn not in raw_data:
                print(f"Column {sn} not found")
                continue
            
            if raw_data[sn].isna().all():
                print(f"Column {sn} is empty and thus ignored")
                continue
                
            s = raw_data[sn].rename(cn)
            
            if 'translate' in cd: s.replace(cd['translate'],inplace=True)
            
            if 'transform' in cd: s = eval(cd['transform'],{ 's':s, 'df':raw_data, 'pd':pd, 'np':np, 'stk':stk , **constants })
            
            if 'categories' in cd: 
                na_sum = s.isna().sum()
                cats = cd['categories'] if cd['categories']!='infer' else [ c for c in s.unique() if pd.notna(c) ]
                s = pd.Series(pd.Categorical(s,categories=cats,ordered=cd['ordered'] if 'ordered' in cd else False), name=cn)
                # Check if the category list provided was comprehensive
                new_nas = s.isna().sum() - na_sum
                if new_nas > 0: print(f'Column {cn} has {new_nas} entries that were not listed in categories')
            gres.append(s)
        if len(gres)==0: continue
        gdf = pd.concat(gres,axis=1)
        gdf.columns = pd.MultiIndex.from_arrays([[group['name']]*len(gdf.columns),gdf.columns])
        res.append(gdf)
    
    df = pd.concat(res,axis=1)
    
    if 'postprocessing' in meta:
        exec(meta['postprocessing'],{'pd':pd, 'np':np, 'stk':stk, 'df':df, **constants  })

    if not multilevel:
        df.columns = df.columns.get_level_values(1)    
    
    return (df, meta) if return_meta else df

# %% ../nbs/01_io.ipynb 6
# Helper functions designed to be used with the annotations

# Convert data_meta into a dict where each group and column maps to their metadata dict
def extract_column_meta(data_meta):
    res = defaultdict(lambda: {})
    for g in data_meta['structure']:
        base = g['scale'] if 'scale' in g else {}
        res[g['name']] = base
        for cd in g['columns']:
            if isinstance(cd,str): cd = [cd]
            res[cd[0]] = {**base,**cd[-1]} if isinstance(cd[-1],dict) else base
    return res

# Convert data_meta into a dict of group_name -> [column names]
def group_columns_dict(data_meta):
    return { g['name'] : [(t[0] if type(t)!=str else t) for t in g['columns']] for g in data_meta['structure'] }

# Take a list and a dict and replace all dict keys in list with their corresponding lists in-place
def list_aliases(lst, da):
    return [ fv for v in lst for fv in (da[v] if v in da else [v]) ]

# %% ../nbs/01_io.ipynb 8
# Creates a mapping old -> new
def get_original_column_names(dmeta):
    res = {}
    for g in dmeta['structure']:
        for c in g['columns']:
            if isinstance(c,str): res[c] = c
            if len(c)==1: res[c[0]] = c[0]
            elif len(c)>=2 and isinstance(c[1],str): res[c[1]] = c[0]
    return res

# Map ot backwards and nt forwards to move from one to the other
def change_mapping(ot, nt, only_matches=False):
    # Todo: warn about non-bijective mappings
    matches = { v: nt[k] for k, v in ot.items() if k in nt and v!=nt[k] } # change those that are shared
    if only_matches: return matches
    else: 
        return { **{ v:k for k, v in ot.items() if k not in nt }, # undo those in ot not in nt
                 **{ k:v for k, v in nt.items() if k not in ot }, # do those in nt not in ot
                 **matches } 

# %% ../nbs/01_io.ipynb 9
# Change an existing dataset to correspond better to a new meta_data
# This is intended to allow making small improvements in the meta even after a model has been run
# It is by no means perfect, but is nevertheless a useful tool to avoid re-running long pymc models for simple column/translation changes
def change_meta_df(df, old_dmeta, new_dmeta):
    print("Warning: this tool handles only simple cases of column name, translation and category order changes.")
    
    # Ready the metafiles for parsing
    old_dmeta = replace_constants(old_dmeta); new_dmeta = replace_constants(new_dmeta)
    
    # Rename columns 
    ocn, ncn = get_original_column_names(old_dmeta), get_original_column_names(new_dmeta)
    name_changes = change_mapping(ocn,ncn,only_matches=True)
    if name_changes != {}: print(f"Renaming columns: {name_changes}")
    df.rename(columns=name_changes,inplace=True)
    
    rev_name_changes = { v: k for k,v in name_changes.items() }
    
    # Get metadata for each column
    ocm = extract_column_meta(old_dmeta)
    ncm = extract_column_meta(new_dmeta)
    
    for c in ncm.keys():
        if c not in df.columns: continue # probably group
        if c not in ocm.keys(): continue # new column
        
        ncd, ocd = ncm[c], ocm[rev_name_changes[c] if c in rev_name_changes else c]
        
        # Warn about transformations and don't touch columns where those change
        if vod(ocd,'transform') != vod(ncd,'transform'):
            print(f"Warning: column {c} has a different transformation. Leaving it unchanged")
            continue
        
        # Handle translation changes
        ot, nt = vod(ocd,'translate',{}), vod(ncd,'translate',{})
        remap = change_mapping(ot,nt)
        if remap != {}: print(f"Remapping {c} with {remap}")
        df[c].replace(remap,inplace=True)
        
        # Reorder categories and/or change ordered status
        if vod(ocd,'categories') != vod(ncd,'categories') or vod(ocd,'ordered') != vod(ncd,'ordered'):
            cats = vod(ncd,'categories')
            if isinstance(cats,list):
                print(f"Changing {c} to Cat({cats},ordered={vod(ncd,'ordered')}")
                df[c] = pd.Categorical(df[c],categories=cats,ordered=vod(ncd,'ordered'))
    
    # column order changes
    gcdict = group_columns_dict(new_dmeta)
    
    cols = ['draw','obs_idx'] + [ c for g in new_dmeta['structure'] for c in gcdict[g['name']]]
    cols = [ c for c in cols if c in df.columns ]
    
    return df[cols]

def change_parquet_meta(orig_file,data_metafile,new_file):
    df, meta = load_parquet_with_metadata(orig_file)
    
    new_data_meta = read_json(data_metafile, replace_const=True)
    df = change_meta_df(df,meta['data'],new_data_meta)
    
    meta['old_data'] = meta['data']
    meta['data'] = new_data_meta
    save_parquet_with_metadata(df,meta,new_file)
    
    return df, meta


# %% ../nbs/01_io.ipynb 10
def read_and_process_data(desc, return_meta=False):
    data, meta = read_annotated_data(desc['file'],return_meta=True)
    
    # Perform transformation and filtering
    if 'preprocessing' in desc: exec(desc['preprocessing'],  {'pd':pd, 'np':np },{ 'df':data })
    if 'filter' in desc: data = data[eval(desc['filter'],    {'pd':pd, 'np':np },{ 'df':data })]
    if 'postprocessing' in desc: exec(desc['postprocessing'],{'pd':pd, 'np':np },{ 'df':data })
    
    return (data, meta) if return_meta else data

# %% ../nbs/01_io.ipynb 12
def save_population_h5(fname,pdf):
    hdf = pd.HDFStore(fname,complevel=9, complib='zlib')
    hdf.put('population',pdf,format='table')
    hdf.close()
    
def load_population_h5(fname):
    hdf =  pd.HDFStore(fname, mode='r')
    res = hdf['population'].copy()
    hdf.close()
    return res

# %% ../nbs/01_io.ipynb 13
def save_sample_h5(fname,trace,COORDS = None, filter_df = None):
    odims = [d for d in trace.predictions.dims if d not in ['chain','draw','obs_idx']]
    
    if COORDS is None: # Recover them from trace (requires posterior be saved in same trace)
        inds = trace.posterior.indexes
        coords = { t: list(inds[t]) for t in inds if t not in ['chain','draw'] and '_dim_' not in t}
        COORDS = { 'immutable': coords, 'mutable': ['obs_idx'] }

    if filter_df is None: # Recover filter dimensions and data from trace (works only for GLMs)
        rmdims = odims + list({'time','unit','combined_inputs'} & set(trace.predictions_constant_data.dims))
        df = trace.predictions_constant_data.drop_dims(rmdims).to_dataframe()#.set_index(demographics_order).indexb
        df.columns = [ s.removesuffix('_id') for s in df.columns]
        df.drop(columns=[c for c in df.columns if c[:4]=='obs_'],inplace=True)

        for d in df.columns:
            if d in COORDS['immutable']:
                fs = COORDS['immutable'][d]
                df[d] = pd.Categorical(df[d].replace(dict(enumerate(fs))),fs)
                if d in orders: df[d] = pd.Categorical(df[d],orders[d],ordered=True)
        filter_df = df

    chains, draws = trace.predictions.dims['chain'], trace.predictions.dims['draw']
    dinds = np.array(list(it.product( range(chains), range(draws), list(filter_df.index)))).reshape( (-1, 3) )

    res_dfs = { 'filter': filter_df }
    for odim in odims:
        response_cols = list(np.array(trace.predictions[odim]))
        xdf = pd.DataFrame(np.concatenate( (
            dinds,
            np.array(trace.predictions['y_'+odim]).reshape( ( -1,len(response_cols) ) )
            ), axis=-1), columns = ['chain', 'draw', 'obs_idx'] + response_cols)
        res_dfs[odim] = postprocess_rdf(xdf,odim)
        
    # Save dfs as hdf5
    hdf = pd.HDFStore(fname,complevel=9, complib='zlib')
    for k,vdf in res_dfs.items():
        hdf.put(k,vdf,format='table')
    hdf.close()


# %% ../nbs/01_io.ipynb 14
# These two very helpful functions are borrowed from https://towardsdatascience.com/saving-metadata-with-dataframes-71f51f558d8e

custom_meta_key = 'salk-toolkit-meta'

def save_parquet_with_metadata(df, meta, file_name):
    table = pa.Table.from_pandas(df)
    
    custom_meta_json = json.dumps(meta)
    existing_meta = table.schema.metadata
    combined_meta = {
        custom_meta_key.encode() : custom_meta_json.encode(),
        **existing_meta
    }
    table = table.replace_schema_metadata(combined_meta)
    
    pq.write_table(table, file_name, compression='GZIP')
    
def load_parquet_with_metadata(file_name,**kwargs):
    restored_table = pq.read_table(file_name,**kwargs)
    restored_df = restored_table.to_pandas()
    restored_meta_json = restored_table.schema.metadata[custom_meta_key.encode()]
    restored_meta = json.loads(restored_meta_json)
    
    return restored_df, restored_meta

# Just load the metadata from the parquet file
# This is currently much more inefficient than it can be as it loads the entire table
def load_parquet_metadata(file_name):
    restored_table = pq.read_table(file_name)
    restored_meta_json = restored_table.schema.metadata[custom_meta_key.encode()]
    return json.loads(restored_meta_json)    
