#@Time      :2019/3/29 10:44
#@Author    :zhounan
# @FileName: main.py
#import numpy as cp
import numpy as np
import cupy as cp
import time
from utils.util import path_exists
from utils.data import Data
from sklearn.metrics.pairwise import rbf_kernel
from sklearn.model_selection import KFold, train_test_split


def ADMM(y_not_j, y_j, rho=0):
    """
    ADMM algriothm for label correlation
    :param y_not_j: cupy, dim {n_instances, n_labels - 1}
        train label data which not contain j col
    :param y_j: cupy, dim {n_instances, 1}
        train label data which only contain j col
    :param rho: ADMM augmented lagrangian parameter(not mentioned in the paper)
    :return: cupy, dim {n_labels - 1, 1}
    """
    #w, _ = cp.linalg.eig(y_not_j.T.dot(y_not_j))
    #rho = 1 / (cp.amax(cp.absolute(w)))
    rho = 1
    #rho = 0.05

    S_j = cp.zeros(y_not_j.shape[1]).reshape(-1, 1)
    z = cp.zeros_like(S_j)
    l1norm = cp.max(cp.abs(cp.dot(y_j.T, y_not_j))) / 100
    u = cp.zeros_like(S_j)
    I = cp.eye(y_not_j.shape[1])

    for iter in range(max_iter):
        S_j = cp.dot(cp.linalg.inv(cp.dot(y_not_j.T, y_not_j) + rho * I), cp.dot(y_not_j.T, y_j) + rho*(z - u))

        omega = l1norm / rho
        a = S_j + u
        z = cp.maximum(0, a - omega) - cp.maximum(0, -a - omega)

        u = u + S_j - z

    return S_j


def CAMEL(S, x, y, alpha, lam2):
    """
    get caml parameter
    :param S: cupy, dim {n_labels, n_labels}
        label correlation matrix
    :param x: numpy, dim {n_instances, n_features}
    :param y: numpy, dim {n_instances, n_labels}
    :param alpha: number
        alpha is the tradeoff parameter that controls the collaboration degree
    :param lam2: number
        λ1 and λ2 are the tradeoff parameters determining the relative importance of the above three terms
        λ1 is given in the paper, equal to 1
    :return:
    """
    num_instances = x.shape[0]
    num_labels = y.shape[1]
    y = cp.array(y)
    G = (1-alpha)*cp.eye(num_labels)+ alpha*S #size num_labels * num_labels
    Z = y #size num_instances * num_labels
    lam1 = 1

    sigma = cp.mean(cp.log(cp.array(rbf_kernel(x, gamma=-1))))
    gamma = np.array((1 / (2 * sigma * sigma)).tolist())
    K = cp.array(rbf_kernel(x, gamma=gamma)) #size num_instances * num_instances

    H = (1 / lam2) * K + cp.eye(num_instances) #size num_instances * num_instances

    for iter in range(max_iter):
        #b_T is row vector
        H_inv = cp.linalg.inv(H) #size num_instances * num_instances
        b_T = cp.sum(cp.dot(H_inv, Z), axis=0) / cp.sum(H_inv)
        b_T = b_T.reshape(1, -1)#size 1 * num_labels

        A = cp.dot(H_inv, Z - b_T.repeat(num_instances, axis=0)) #size num_instances * num_labels

        T = (1 / lam2) * cp.dot(K, A) + b_T.repeat(num_instances, axis=0) #size num_instances * num_labels
        Z = cp.dot((T + lam1 * cp.dot(y, G.T)), cp.linalg.inv(cp.eye(num_labels) + lam1 * cp.dot(G, G.T)))

    return G, A, gamma, b_T

def predict(G, A, gamma, x, x_test, b_T):
    num_instances = x.shape[0]
    num_labels = G.shape[0]
    b = b_T.T
    #a col vector
    K_test = cp.array(rbf_kernel(x, x_test, gamma=gamma)) # size n_train * n_test
    temp = cp.dot(K_test.T, A) #(n_test * n_train) * (n_train * n_label) = (n_test * n_label)
    temp = temp.T # (n_label * n_test)

    logits = cp.dot(G.T, temp + b) # (n_label * n_test)
    logits[logits > 0] = 1
    logits[logits < 0] = -1
    logits = logits.T #(n_test * n_label)
    return logits

#Mean square error
def loss(predcit, y):
    y = cp.array(y)
    loss = cp.sum(cp.square(y-predcit), axis=1)
    loss = cp.mean(loss)
    return loss

if __name__ == '__main__':
    dataset = 'yeast'
    data = Data(dataset, label_type=0)
    x, y = data.load_data()
    train_log_path = '../train_log/' + dataset + '/'

    num_labels = y.shape[1]
    S = cp.zeros((num_labels, num_labels))
    max_iter = 1000
    kf = KFold(n_splits=5, random_state=1)

    #trade-off para
    rho_list = [1]
    alpha_list = cp.arange(0, 1, 0.1)
    lam2_list = cp.array([0.001, 0.002, 0.01, 0.02, 0.1, 0.2, 1])
    step = 0
    best_val_loss = 1e6

    for rho in rho_list:
        for alpha in alpha_list:
            for lam2 in lam2_list:
                output = 'step:{}, time:{}, CAME rho:{}, alpha:{}, lam2:{}'.format(step, time.strftime('%H:%M:%S', time.localtime(time.time())), rho, alpha, lam2)
                output_ = output
                print(output)

                fold = 0
                train_loss_list = []
                val_loss_list = []
                test_loss_list = []
                for train_idx, test_idx in kf.split(x):
                    x_trainval = x[train_idx]
                    y_trainval = y[train_idx]
                    x_train, x_val, y_train, y_val = train_test_split(x_trainval, y_trainval, test_size=0.2,
                                                                      random_state=0)

                    x_test = x[test_idx]
                    y_test = y[test_idx]

                    # get label correlation matrix
                    for j in range(num_labels):
                        y_j = cp.array(y_train[:, j].reshape(-1, 1))
                        y_not_j = cp.array(np.delete(y_train, j, axis=1))
                        S_j = ADMM(y_not_j, y_j, rho)
                        S[:, j] = cp.array(np.insert(np.array(S_j.tolist()), j, 0))

                    # get caml parameter
                    G, A, gamma, b_T = CAMEL(S, x_train, y_train, alpha, lam2)

                    #evalue
                    train_predict = predict(G, A, gamma, x_train, x_train, b_T)
                    val_predict = predict(G, A, gamma, x_train, x_val, b_T)
                    test_predict = predict(G, A, gamma, x_train, x_test, b_T)
                    train_loss = loss(train_predict, y_train)
                    val_loss = loss(val_predict, y_val)
                    test_loss = loss(test_predict, y_test)

                    train_loss_list.append(train_loss)
                    val_loss_list.append(val_loss)
                    test_loss_list.append(test_loss)

                    output = 'time:{}, Kflod {}, train loss:{}, val loss:{}, test loss:{}'.format(time.strftime('%H:%M:%S', time.localtime(time.time())), fold, train_loss, val_loss, test_loss)
                    output_ = output_ + '\n'+ output
                    print(output)
                    fold += 1

                mean_train_loss, mean_val_loss, mean_test_loss = cp.mean(train_loss_list), cp.mean(train_loss_list), cp.mean(train_loss_list)
                output = 'mean train loss:{}, val loss:{}, test loss:{}'.format(mean_train_loss, mean_val_loss, mean_test_loss)
                output_ = output_ + '\n' + output
                print(output)

                if mean_val_loss < best_val_loss:
                    best_val_loss = mean_val_loss
                    output = 'current best para, rho:{}, alpha:{}, lam2:{}'.format(rho, alpha, lam2)
                    output_ = output_ + '\n' + output
                    print(output)

                step += 1
                output_ = output_ + '\n'
                print()

                log_name = time.strftime('%Y-%m-%d%H:%M:%S', time.localtime(time.time())) + '.log'
                path_exists(train_log_path+log_name)
                with open(train_log_path+log_name, 'a+') as f:
                    f.write(output_)

