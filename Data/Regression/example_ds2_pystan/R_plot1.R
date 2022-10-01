
setwd("D:/My_Study/Gaussian Process & RVT Study/ngmm_tools/Data/Regression/example_ds2_pystan")

data <- read.table('pvb_regression_ds2_pystan_type2_stan_posterior_raw.csv', header=TRUE, sep= ",", na.strings="NA", dec=".", strip.white=TRUE)

head(data)

par(mfrow=c(2,2))

par(fig=c(0.1,0.7,0.3,0.9))
hist(data$dc_0, breaks = 50,
     main = "None",
     xlab = "dc_0",
     ylab = "Count",
     freq = FALSE)

par(fig=c(0.8,1,0,1), new=TRUE)
hist(data$ell_1e)

par(fig=c(0.1,0.67,0.1,0.25), new=TRUE)
hist(data$ell_1as)
