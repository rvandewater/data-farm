# Sampler for the jobs to execute
import warnings

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from simple_term_menu import TerminalMenu

from generator_labeler.ActiveModel.ActiveQuantileForest import QuantileForestModel
from generator_labeler.CustomActiveLearning.ActiveLearningUtility import ActiveLearningUtility, JobExecutionSampler, UserSampling
from generator_labeler.JobExecutionSampler.supervised_sampler import UserSampler

class CustomActiveLearning:

    def __init__(self):
        print("Initiating Custom Active Learning")

    def run_active_learning(self, features_df, feature_cols, label_col, n_iter=20, max_early_stop=2, early_stop_th=0.1,
                            verbose=False, user_prompt=False, sampler=JobExecutionSampler.USER_SPECIFIED):
        warnings.filterwarnings("ignore")

        data_size = []
        test_scores = []
        cross_validation_scores = []
        test_scores_exp = []
        cross_validation_scores_exp = []
        IQRs_mean = []
        iterations_results = []
        early_stop_count = 0

        # Start Active-Learning
        X_train, y_train, ids_train, X_test, _, ids_test = ActiveLearningUtility.get_dataset(features_df, feature_cols, label_col)

        # -> create model
        # -> predict labels
        # -> next iteration

        for idx in range(n_iter):
            if(user_prompt):
                val = input("Enter x to stop AL process, press any other key to continue: ")
                if(val.strip() == "x"):
                    break

            print("======= Iteration", idx)

            data_size.append(X_train.shape[0])
            print("Train:", X_train.shape)
            print("Test:", X_test.shape)

            iter_res = self.active_learning_iteration(X_train, y_train, ids_train, X_test, ids_test, feature_cols,
                                                      verbose=verbose)

            # store info
            cross_validation_scores.append(iter_res["model"].cross_validation_scores)
            test_scores_exp.append(iter_res["model"].test_scores_exp)
            cross_validation_scores_exp.append(iter_res["model"].cross_validation_scores_exp)
            IQRs_mean.append(np.mean(np.abs(iter_res["uncertainty_interval"])))
            iter_res["model"] = str(iter_res["model"])
            iterations_results.append(iter_res)

            if (idx + 1 >= n_iter):
                print("Max iteration reached!")
                break

            if self.check_early_stop(iterations_results, early_stop_th):
                early_stop_count += 1
                if early_stop_count >= max_early_stop:
                    print("Early stop reached!")
                    break
                else:
                    print(f">>> Skip early stop {early_stop_count}. Max early stop is set to {max_early_stop}.")


            # Prepare next iteration
            if sampler is JobExecutionSampler.RANDOM_SAMPLER:
                IRQ_th = np.quantile(iter_res["uncertainty_interval"], 0.95)
                len_new_X_train = len(X_test[iter_res["uncertainty_interval"] > IRQ_th])
                sampling_idx = np.random.randint(0, len(X_test), len_new_X_train)
                new_ids_train = ids_test.iloc[sampling_idx].copy()

            elif sampler is JobExecutionSampler.UNCERTAINTY:  # Sampling based on uncertainty threshold
                IRQ_th = np.quantile(iter_res["uncertainty_interval"], 0.95)
                new_ids_train = ids_test.iloc[iter_res["uncertainty_interval"] > IRQ_th].copy()

            elif sampler is JobExecutionSampler.USER_SPECIFIED:
                # Custom job sampling; sample jobs inputted by user
                user_sampling = UserSampling()
                new_ids_train = user_sampling.user_specified_sampling(X_test, iter_res, ids_test)

            if len(new_ids_train) == 0:
                print("No more jobs to run, Early Stop!")
                break

            print("Candidates to run:\n", new_ids_train)

            # -> RUN Jobs
            new_jobs_to_run = new_ids_train.iloc[:, 0].values
            ActiveLearningUtility.submit_jobs(new_jobs_to_run)

            # -> Collect exec time
            executed_jobs_runtime = ActiveLearningUtility.get_executed_plans_exec_time(new_jobs_to_run)
            for k, v in executed_jobs_runtime.iterrows():
                features_df.loc[k, "netRunTime"] = v.values[0]
            features_df[label_col] = np.log(features_df["netRunTime"])

            X_train, y_train, ids_train, X_test, _, ids_test = ActiveLearningUtility.get_dataset(features_df, feature_cols,
                                                                                                 label_col)

            print("=====================================================")

        pred_jobs = pd.DataFrame(iterations_results[-1]["test_ids"])
        pred_jobs[f"pred_{label_col}"] = iterations_results[-1]["pred_labels"]
        pred_jobs[f"unc_low_{label_col}"] = iterations_results[-1]["uncertainty_low"]
        pred_jobs[f"unc_up_{label_col}"] = iterations_results[-1]["uncertainty_high"]
        pred_jobs = pred_jobs.set_index(["plan_id", "data_id"])
        final_dataset = pd.merge(features_df, pred_jobs, left_index=True, right_index=True, how="left")

        results = {
            "iterations": list(range(n_iter)),
            "data_size": data_size,
            "model_uncertainty": IQRs_mean,
            "test_scores": test_scores,
            "test_scores_exp": test_scores_exp,
            "cross_validation_scores": cross_validation_scores,
            "cross_validation_scores_exp": cross_validation_scores_exp,
            "iterations_results": iterations_results,
            "final_dataset": final_dataset

        }
        return results

    def active_learning_iteration(self, X_train, y_train, ids_train, X_test, ids_test, feature_cols, verbose=False):
        if X_train.__len__() != ids_train.__len__():
            raise Exception("x_train does not match ids_train")

        if X_test.__len__() != ids_test.__len__():
            raise Exception("x_test does not match ids_test")

        results = {}
        qf_model = QuantileForestModel(random_state=42)
        qf_model.fit(X_train, y_train)
        qf_model.cross_validate(X_train, y_train)

        y_pred = qf_model.predict(X_test)
        y_pred_upper = qf_model.predict(X_test, quantile=75)
        y_pred_lower = qf_model.predict(X_test, quantile=25)

        if verbose:
            p = y_pred.argsort()
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.plot(y_pred[p], marker=".", linewidth=1, label="y_true", color="#1f77b4")
            ax.errorbar(np.arange(len(y_pred)), y_pred[p],
                        yerr=np.array([y_pred[p] - y_pred_lower[p], y_pred_upper[p] - y_pred[p]]), linewidth=0.5,
                        fmt='.',
                        color="#ff7f0e", label="Pred. interval")
            # ax.set_title(f"{type(qf_model).__name__} - Score[r2]: {qf_model.test_scores['r2']:.2f}")
            ax.set_ylabel("Log(Runtime)")
            ax.set_xlabel("Test jobs")
            ax.legend()
            #  plt.show()
            plt.close()

            fig, ax = plt.subplots(figsize=(10, 6))
            # ax.plot(np.exp(y_pred[p]), marker=".", linewidth=1, label="y_true", color="#1f77b4")
            ax.errorbar(np.arange(len(y_pred)), np.exp(y_pred[p]), yerr=np.array(
                [np.exp(y_pred[p]) - np.exp(y_pred_lower[p]), np.exp(y_pred_upper[p]) - np.exp(y_pred[p])]),
                        linewidth=0.5,
                        fmt='.', color="#ff7f0e", label="Pred. interval")
            # ax.set_title(f"EXP - {type(qf_model).__name__} - Score[r2]: {qf_model.test_scores_exp['r2']:.2f}")
            ax.set_ylabel("Runtime [ms]")
            ax.set_xlabel("Test jobs")
            ax.legend()
            #  plt.show()
            plt.close()

            # display(pd.DataFrame({"Feature": feature_cols, "F. Importance": qf_model.model.feature_importances_}) \
            #        .sort_values("F. Importance", ascending=False).head(15).style.background_gradient())

        IQR_interval = qf_model.predict_model_uncertainty(X_test, verbose=True)

        results["model"] = qf_model
        results["train_ids"] = ids_train.to_dict(orient="row")
        results["test_ids"] = ids_test.to_dict(orient="row")
        results["train_labels"] = y_train
        # results["test_labels"] = y_test
        results["pred_labels"] = y_pred
        results["uncertainty_high"] = y_pred_upper
        results["uncertainty_low"] = y_pred_lower
        results["uncertainty_interval"] = IQR_interval
        results["feature_importance"] = {"Feature": feature_cols,
                                         "F_Importance": qf_model.model.feature_importances_}

        return results

    def active_learning_iteration(self, X_train, y_train, ids_train, X_test, ids_test, feature_cols, verbose=False):
        if X_train.__len__() != ids_train.__len__():
            raise Exception("x_train does not match ids_train")

        if X_test.__len__() != ids_test.__len__():
            raise Exception("x_test does not match ids_test")

        results = {}
        qf_model = QuantileForestModel(random_state=42)
        qf_model.fit(X_train, y_train)
        qf_model.cross_validate(X_train, y_train)

        y_pred = qf_model.predict(X_test)
        y_pred_upper = qf_model.predict(X_test, quantile=75)
        y_pred_lower = qf_model.predict(X_test, quantile=25)

        if verbose:
            p = y_pred.argsort()
            fig, ax = plt.subplots(figsize=(10, 6))
            ax.plot(y_pred[p], marker=".", linewidth=1, label="y_true", color="#1f77b4")
            ax.errorbar(np.arange(len(y_pred)), y_pred[p],
                        yerr=np.array([y_pred[p] - y_pred_lower[p], y_pred_upper[p] - y_pred[p]]), linewidth=0.5,
                        fmt='.',
                        color="#ff7f0e", label="Pred. interval")
            # ax.set_title(f"{type(qf_model).__name__} - Score[r2]: {qf_model.test_scores['r2']:.2f}")
            ax.set_ylabel("Log(Runtime)")
            ax.set_xlabel("Test jobs")
            ax.legend()
            #  plt.show()
            plt.close()

            fig, ax = plt.subplots(figsize=(10, 6))
            # ax.plot(np.exp(y_pred[p]), marker=".", linewidth=1, label="y_true", color="#1f77b4")
            ax.errorbar(np.arange(len(y_pred)), np.exp(y_pred[p]), yerr=np.array(
                [np.exp(y_pred[p]) - np.exp(y_pred_lower[p]), np.exp(y_pred_upper[p]) - np.exp(y_pred[p])]),
                        linewidth=0.5,
                        fmt='.', color="#ff7f0e", label="Pred. interval")
            # ax.set_title(f"EXP - {type(qf_model).__name__} - Score[r2]: {qf_model.test_scores_exp['r2']:.2f}")
            ax.set_ylabel("Runtime [ms]")
            ax.set_xlabel("Test jobs")
            ax.legend()
            #  plt.show()
            plt.close()

            # display(pd.DataFrame({"Feature": feature_cols, "F. Importance": qf_model.model.feature_importances_}) \
            #        .sort_values("F. Importance", ascending=False).head(15).style.background_gradient())

        IQR_interval = qf_model.predict_model_uncertainty(X_test, verbose=True)

        results["model"] = qf_model
        results["train_ids"] = ids_train.to_dict(orient="row")
        results["test_ids"] = ids_test.to_dict(orient="row")
        results["train_labels"] = y_train
        # results["test_labels"] = y_test
        results["pred_labels"] = y_pred
        results["uncertainty_high"] = y_pred_upper
        results["uncertainty_low"] = y_pred_lower
        results["uncertainty_interval"] = IQR_interval
        results["feature_importance"] = {"Feature": feature_cols, "F_Importance": qf_model.model.feature_importances_}

        return results

    def check_early_stop(self, iterations_results, th=0.1):
        IQRs_RMSE = np.array(
            [np.mean(np.exp(I["uncertainty_high"]) - np.exp(I["uncertainty_low"])) for I in iterations_results])
        # IQRs_std = np.array([np.std(np.exp(I["uncertainty_high"]) - np.exp(I["uncertainty_low"])) for I in iterations_results])
        print(">>> Model's uncertanties: ", IQRs_RMSE)
        if len(IQRs_RMSE) < 2:
            return False

        min_u = IQRs_RMSE[-2]
        min_local_u = IQRs_RMSE[-2]
        r = IQRs_RMSE[-1] / min_local_u

        if (r > 1) or (IQRs_RMSE[-1] > min_u):
            return False

        if (1 - r) < th:
            return False
        return True






