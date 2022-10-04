__author__ = "konwar.m"
__copyright__ = "Copyright 2022, AI R&D"
__credits__ = ["konwar.m"]
__license__ = "Individual Ownership"
__version__ = "1.0.1"
__maintainer__ = "konwar.m"
__email__ = "rickykonwar@gmail.com"
__status__ = "Development"

import os
import copy
import json
import numpy as np
import pandas as pd

import torch

from tqdm import tqdm
from sklearn import model_selection

from transformers import AdamW
from transformers import get_linear_schedule_with_warmup

import config
import dataset
import engine
from model import QuestionAnsweringModel

def add_end_index(df, is_train=True):
    df_modified = df.copy()
    if is_train:
        df_modified['answer_end'] = np.nan 

    final_list = []
    for row_Index, row_Data in tqdm(df.iterrows(), total=df.shape[0], desc='Adding_answer_end_indexes'):
        if is_train:
            try:
                gold_text = row_Data.text
                start_idx = row_Data.answer_start
                end_idx = start_idx + len(gold_text)

                if row_Data.context[start_idx:end_idx] == gold_text:
                    df_modified.iloc[row_Index, df_modified.columns.get_loc('answer_end')] = end_idx
                else:
                    for n in [1, 2]:
                        if context[start_idx-n:end_idx-n] == gold_text:
                            # this means the answer is off by 'n' tokens
                            df_modified.iloc[row_Index, df_modified.columns.get_loc('answer_start')] = start_idx - n
                            df_modified.iloc[row_Index, df_modified.columns.get_loc('answer_end')] = end_idx - n
            except Exception as ex:
                continue
        else:
            try:
                answer_list = row_Data.answers
                answer_final_list = copy.deepcopy(answer_list)
                if isinstance(answer_list, list):
                    count=0
                    for ans in answer_list:
                        gold_text = ans['text']
                        start_idx = ans['answer_start']
                        end_idx = start_idx + len(gold_text)

                        if row_Data.context[start_idx:end_idx] == gold_text:
                            answer_final_list[count]['answer_end'] = end_idx
                        else:
                            for n in [1, 2]:
                                if context[start_idx-n:end_idx-n] == gold_text:
                                    # this means the answer is off by 'n' tokens
                                    answer_final_list[count]['answer_start'] = start_idx - n
                                    answer_final_list[count]['answer_end'] = end_idx - n
                        count+=1
                    
                # df_modified.iloc[row_Index, df_modified.columns.get_loc('answers')] = answer_final_list
                final_list.append(answer_final_list)
            except Exception as ex:
                print(ex)
                continue
    
    if not is_train:
        df_modified['answers'] = final_list

    df_modified = df_modified.rename(columns={'text': 'answer', 'index': 'id'})
    return df_modified

def process_data(input_file_path, is_train=True, record_path = ['data','paragraphs','qas','answers'], verbose = 1):
    """
    input_file_path: path to the squad json file.
    record_path: path to deepest level in json file default value is
    ['data','paragraphs','qas','answers']
    verbose: 0 to suppress it default is 1
    """
    if os.path.exists(config.TRAINING_FILE) and is_train:
        df = pd.read_csv(config.TRAINING_FILE)
        return df
    elif os.path.exists(config.VALID_FILE) and not is_train:
        df = pd.read_csv(config.VALID_FILE)
        return df
    else:
        if verbose:
            print("Reading the json file")    
        file = json.loads(open(input_file_path).read())
        if verbose:
            print("processing...")
        # parsing different level's in the json file
        js = pd.json_normalize(file, record_path)
        m = pd.json_normalize(file, record_path[:-1])
        r = pd.json_normalize(file,record_path[:-2])
        
        if is_train:
            # Combining it into single dataframe
            idx = np.repeat(r['context'].values, r.qas.str.len())
            ndx = np.repeat(m['id'].values,m['answers'].str.len())
            m['context'] = idx
            js['q_idx'] = ndx
            main = pd.concat([ m[['id','question','context']].set_index('id'),js.set_index('q_idx')],1,sort=False).reset_index()
            main['c_id'] = main['context'].factorize()[0]
            if verbose:
                print("shape of the dataframe is {}".format(main.shape))
                print("Done")

            # Getting answer ending index
            main = add_end_index(df=main)

            main.to_csv(config.TRAINING_FILE, index=False) 
            return main
        else:
            # Combining it into single dataframe
            idx = np.repeat(r['context'].values, r.qas.str.len())
            m['context'] = idx
            main = m[['id','question','context','answers']].set_index('id').reset_index()
            main['c_id'] = main['context'].factorize()[0]
            if verbose:
                print("shape of the dataframe is {}".format(main.shape))
                print("Done")
            
            # Getting answer ending index
            main = add_end_index(df=main, is_train=False)

            main.to_csv(config.VALID_FILE, index=False)
            return main

if __name__ == "__main__":
    df_train = process_data(config.QnA_TRAINING_PATH)
    df_valid = process_data(config.QnA_VALIDATION_PATH, is_train=False)

    df_train = df_train.reset_index(drop=True)
    df_valid = df_valid.reset_index(drop=True)

    train_dataset = dataset.QuestionAnsweringDataset(
        data=df_train
    )

    train_data_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=config.TRAIN_BATCH_SIZE, num_workers=4, shuffle=True
    )

    valid_dataset = dataset.QuestionAnsweringDataset(
        data=df_valid
    )

    valid_data_loader = torch.utils.data.DataLoader(
        valid_dataset, batch_size=config.VALID_BATCH_SIZE, num_workers=1, shuffle=True
    )

    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda" if use_cuda else "cpu")

    model = QuestionAnsweringModel()
    model.to(device)

    param_optimizer = list(model.named_parameters())
    no_decay = ["bias", "LayerNorm.bias", "LayerNorm.weight"]
    optimizer_parameters = [
        {
            "params": [
                p for n, p in param_optimizer if not any(nd in n for nd in no_decay)
            ],
            "weight_decay": 0.001,
        },
        {
            "params": [
                p for n, p in param_optimizer if any(nd in n for nd in no_decay)
            ],
            "weight_decay": 0.0,
        },
    ]

    num_train_steps = int(len(df_train) / config.TRAIN_BATCH_SIZE * config.EPOCHS)
    optimizer = AdamW(optimizer_parameters, lr=3e-5)
    scheduler = get_linear_schedule_with_warmup(
        optimizer, num_warmup_steps=0, num_training_steps=num_train_steps
    )

    best_f1 = 0
    for epoch in range(config.EPOCHS):
        train_em, train_f1 = engine.train_fn(train_data_loader, model, optimizer, device, scheduler)
        test_em, test_f1 = engine.test_fn(valid_data_loader, model, device)
        print(f"Train F1 = {train_f1} Valid F1 = {test_f1}")
        if test_f1 > best_f1:
            torch.save(model.state_dict(), config.MODEL_PATH)
            best_f1 = test_f1